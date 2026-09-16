from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradier_collector.contract_tracker import (
    ContractTracker,
    CorruptMetadataError,
)

DAY = date(2026, 9, 15)
MORNING = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
NOON = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)


def contracts(*strikes: float) -> list[dict[str, object]]:
    return [
        {"symbol": f"SPY260915C{int(s * 1000):08d}", "strike": s, "option_type": "call"}
        for s in strikes
    ]


def make_tracker(path: Path, strict: bool = False) -> ContractTracker:
    tracker = ContractTracker(path, ticker="SPY", trading_date=DAY, strict=strict)
    tracker.load()
    return tracker


def test_initial_contracts_are_added(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path / "c.json")
    new = tracker.add_contracts(contracts(600, 601), expiration="2026-09-15", discovered_utc=MORNING)
    assert len(new) == 2
    assert len(tracker) == 2
    assert new[0].first_discovered_utc == MORNING.isoformat()


def test_duplicates_are_ignored_and_keep_discovery_time(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path / "c.json")
    tracker.add_contracts(contracts(600), expiration="2026-09-15", discovered_utc=MORNING)
    again = tracker.add_contracts(contracts(600), expiration="2026-09-15", discovered_utc=NOON)
    assert again == []
    symbol = tracker.symbols()[0]
    contract = tracker.get(symbol)
    assert contract is not None
    assert contract.first_discovered_utc == MORNING.isoformat()


def test_new_contracts_join_without_removing_old_ones(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path / "c.json")
    tracker.add_contracts(contracts(590, 600), expiration="2026-09-15", discovered_utc=MORNING)
    tracker.add_contracts(contracts(610, 620), expiration="2026-09-15", discovered_utc=NOON)
    assert len(tracker) == 4
    # The early, now far out-of-the-money strikes are still tracked.
    assert any("00590000" in s for s in tracker.symbols())


def test_universe_is_persisted_and_restored_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    tracker = make_tracker(path)
    tracker.add_contracts(contracts(600, 601), expiration="2026-09-15", discovered_utc=MORNING)

    restarted = make_tracker(path)
    assert restarted.symbols() == tracker.symbols()
    assert len(restarted) == 2
    # And a restart mid-session keeps adding on top of the restored universe.
    restarted.add_contracts(contracts(602), expiration="2026-09-15", discovered_utc=NOON)
    assert len(make_tracker(path)) == 3


def test_persisted_records_carry_the_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    tracker = make_tracker(path)
    tracker.add_contracts(contracts(600), expiration="2026-09-15", discovered_utc=MORNING)
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["contracts"][0]
    assert set(record) >= {
        "symbol",
        "ticker",
        "expiration",
        "strike",
        "option_type",
        "first_discovered_utc",
    }
    assert payload["trading_date"] == DAY.isoformat()


def test_corrupt_metadata_is_quarantined_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    tracker = make_tracker(path)
    assert len(tracker) == 0
    preserved = list(tmp_path.glob("c.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "{not json"


def test_strict_mode_raises_on_corrupt_metadata(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CorruptMetadataError):
        make_tracker(path, strict=True)


def test_metadata_from_another_day_is_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"trading_date": "2026-09-14", "contracts": []}), encoding="utf-8"
    )
    tracker = make_tracker(path)
    assert len(tracker) == 0
    assert list(tmp_path.glob("c.json.corrupt-*"))


def test_unusable_records_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "trading_date": DAY.isoformat(),
                "contracts": [
                    {"symbol": "GOOD", "strike": 1.0, "option_type": "call"},
                    {"strike": 2.0},
                    "nonsense",
                ],
            }
        ),
        encoding="utf-8",
    )
    tracker = make_tracker(path)
    assert tracker.symbols() == ["GOOD"]


def test_new_trading_day_starts_empty(tmp_path: Path) -> None:
    path_today = tmp_path / "2026-09-15_contracts.json"
    tracker = ContractTracker(path_today, ticker="SPY", trading_date=DAY)
    tracker.add_contracts(contracts(600), expiration="2026-09-15", discovered_utc=MORNING)

    path_tomorrow = tmp_path / "2026-09-16_contracts.json"
    tomorrow = ContractTracker(path_tomorrow, ticker="SPY", trading_date=date(2026, 9, 16))
    assert len(tomorrow) == 0


def test_contracts_without_symbols_are_skipped(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path / "c.json")
    new = tracker.add_contracts(
        [{"strike": 600.0, "option_type": "call"}, {"symbol": "  "}],
        expiration="2026-09-15",
        discovered_utc=MORNING,
    )
    assert new == []
    assert len(tracker) == 0
