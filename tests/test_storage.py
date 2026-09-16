from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tradier_collector import storage
from tradier_collector.schemas import OPTION_SCHEMA, UNDERLYING_SCHEMA

DAY = date(2026, 9, 15)
TS = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


def option_row(cycle: str = "c1", symbol: str = "SPY1", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "cycle_id": cycle,
        "poll_timestamp_utc": TS,
        "request_started_utc": TS,
        "request_completed_utc": TS,
        "ticker": "SPY",
        "expiration": "2026-09-15",
        "underlying_spot": 600.25,
        "symbol": symbol,
        "strike": 600.0,
        "option_type": "call",
        "bid": 1.2,
        "bid_size": 10,
        "ask": 1.3,
        "ask_size": 12,
        "last": 1.25,
        "volume": 100,
        "open_interest": 500,
    }
    row.update(overrides)
    return row


def underlying_row(cycle: str = "c1", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "cycle_id": cycle,
        "poll_timestamp_utc": TS,
        "ticker": "SPY",
        "bid": 600.2,
        "ask": 600.3,
        "last": 600.25,
        "volume": 1_000_000,
    }
    row.update(overrides)
    return row


def test_initial_write_and_reload(tmp_path: Path) -> None:
    written = storage.append_option_snapshot(
        [option_row()], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    assert written == 1
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert len(frame) == 1
    assert frame.iloc[0]["symbol"] == "SPY1"


def test_append_accumulates_cycles(tmp_path: Path) -> None:
    storage.append_option_snapshot([option_row("c1", "A")], data_dir=tmp_path, ticker="SPY", day=DAY)
    storage.append_option_snapshot(
        [option_row("c2", "A"), option_row("c2", "B")], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert len(frame) == 3
    assert set(frame["cycle_id"]) == {"c1", "c2"}


def test_duplicate_cycle_and_symbol_is_replaced_not_duplicated(tmp_path: Path) -> None:
    storage.append_option_snapshot(
        [option_row("c1", "A", bid=1.0)], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    storage.append_option_snapshot(
        [option_row("c1", "A", bid=2.0)], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert len(frame) == 1
    assert frame.iloc[0]["bid"] == 2.0


def test_underlying_dedupe_key(tmp_path: Path) -> None:
    storage.append_underlying_snapshot(
        [underlying_row("c1", last=1.0)], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    storage.append_underlying_snapshot(
        [underlying_row("c1", last=2.0), underlying_row("c2")],
        data_dir=tmp_path,
        ticker="SPY",
        day=DAY,
    )
    frame = storage.load_underlying_day(tmp_path, "SPY", DAY)
    assert len(frame) == 2
    assert frame.loc[frame["cycle_id"] == "c1", "last"].iloc[0] == 2.0


def test_null_values_do_not_change_the_persisted_schema(tmp_path: Path) -> None:
    storage.append_option_snapshot(
        [option_row("c1", "A", volume=None, open_interest=None, bid=None, bid_size=None)],
        data_dir=tmp_path,
        ticker="SPY",
        day=DAY,
    )
    storage.append_option_snapshot(
        [option_row("c2", "A")], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    schema = pq.read_schema(storage.option_path(tmp_path, "SPY", DAY))
    assert schema.field("volume").type == pa.int64()
    assert schema.field("open_interest").type == pa.int64()
    assert schema.field("tradier_delta").type == pa.float64()
    assert schema.field("poll_timestamp_utc").type == pa.timestamp("us", tz="UTC")


def test_empty_rows_write_nothing(tmp_path: Path) -> None:
    assert storage.append_option_snapshot([], data_dir=tmp_path, ticker="SPY", day=DAY) == 0
    assert not storage.option_path(tmp_path, "SPY", DAY).exists()


def test_missing_day_loads_an_empty_frame_with_all_columns(tmp_path: Path) -> None:
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert frame.empty
    assert list(frame.columns) == list(OPTION_SCHEMA.names)
    under = storage.load_underlying_day(tmp_path, "SPY", DAY)
    assert list(under.columns) == list(UNDERLYING_SCHEMA.names)


def test_write_failure_preserves_the_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage.append_option_snapshot(
        [option_row("c1", "A", bid=1.0)], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    path = storage.option_path(tmp_path, "SPY", DAY)
    original = path.read_bytes()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tradier_collector.storage.pq.write_table", boom)
    with pytest.raises(OSError):
        storage.append_option_snapshot(
            [option_row("c2", "A")], data_dir=tmp_path, ticker="SPY", day=DAY
        )

    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp-*"))
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert len(frame) == 1


def test_replacement_is_atomic_via_os_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        seen["src"] = Path(src)
        seen["dst"] = Path(dst)
        real_replace(src, dst)

    monkeypatch.setattr("tradier_collector.storage.os.replace", spy)
    storage.append_option_snapshot([option_row()], data_dir=tmp_path, ticker="SPY", day=DAY)
    # The temp file must live in the destination directory, or os.replace is
    # not atomic across filesystems.
    assert seen["src"].parent == seen["dst"].parent
    assert storage.TEMP_SUFFIX in seen["src"].name


def test_stale_temp_files_are_cleaned_but_fresh_ones_survive(tmp_path: Path) -> None:
    directory = tmp_path / "SPY" / "options"
    directory.mkdir(parents=True)
    stale = directory / f"2026-09-15.parquet{storage.TEMP_SUFFIX}123-abcd"
    fresh = directory / f"2026-09-15.parquet{storage.TEMP_SUFFIX}456-efgh"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")

    old = 1_600_000_000
    os.utime(stale, (old, old))

    removed = storage.clean_stale_temp_files(directory)
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_schema_drift_is_detected_and_the_file_is_not_replaced(tmp_path: Path) -> None:
    path = storage.option_path(tmp_path, "SPY", DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"unexpected": [1, 2, 3]}), path)
    before = path.read_bytes()
    with pytest.raises(storage.SchemaDriftError):
        storage.append_option_snapshot([option_row()], data_dir=tmp_path, ticker="SPY", day=DAY)
    assert path.read_bytes() == before


def test_rows_are_sorted_by_poll_timestamp(tmp_path: Path) -> None:
    later = TS.replace(minute=35)
    storage.append_option_snapshot(
        [option_row("c2", "A", poll_timestamp_utc=later)],
        data_dir=tmp_path,
        ticker="SPY",
        day=DAY,
    )
    storage.append_option_snapshot(
        [option_row("c1", "A")], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert list(frame["cycle_id"]) == ["c1", "c2"]


def test_ensure_layout_creates_the_tree(tmp_path: Path) -> None:
    storage.ensure_layout(tmp_path, ["SPY", "QQQ"])
    for ticker in ("SPY", "QQQ"):
        for sub in ("options", "underlying", "metadata"):
            assert (tmp_path / ticker / sub).is_dir()
    assert (tmp_path / "health").is_dir()


def test_extra_keys_in_rows_are_ignored(tmp_path: Path) -> None:
    storage.append_option_snapshot(
        [option_row(**{"not_a_column": "ignored"})], data_dir=tmp_path, ticker="SPY", day=DAY
    )
    frame = storage.load_option_day(tmp_path, "SPY", DAY)
    assert "not_a_column" not in frame.columns
