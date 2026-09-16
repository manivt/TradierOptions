"""QA calculations are checked against synthetic days with known defects."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import qa_report
from config import Settings
from tradier_collector import storage
from tradier_collector.contract_tracker import ContractTracker
from tradier_collector.market_clock import build_market_clock

DAY = date(2026, 9, 15)
OPEN = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


def build_day(
    data_dir: Path,
    *,
    cycles: int = 406,
    contracts: int = 42,
    ticker: str = "SPY",
    skip_cycles: set[int] | None = None,
    null_quote_cycles: set[int] | None = None,
    thin_cycle: int | None = None,
    write_metadata: bool = True,
    write_underlying: bool = True,
) -> None:
    """Create a synthetic trading day with optional, deliberate defects."""
    skip_cycles = skip_cycles or set()
    null_quote_cycles = null_quote_cycles or set()
    option_rows: list[dict[str, Any]] = []
    underlying_rows: list[dict[str, Any]] = []

    for index in range(cycles):
        stamp = OPEN + timedelta(minutes=index)
        cycle_id = f"cyc-{index:04d}"
        if write_underlying:
            underlying_rows.append(
                {
                    "cycle_id": cycle_id,
                    "poll_timestamp_utc": stamp,
                    "ticker": ticker,
                    "bid": 600.0,
                    "ask": 600.1,
                    "last": 600.05,
                    "volume": 1000,
                }
            )
        if index in skip_cycles:
            continue
        count = 2 if thin_cycle == index else contracts
        for contract in range(count):
            quoted = index not in null_quote_cycles
            option_rows.append(
                {
                    "cycle_id": cycle_id,
                    "poll_timestamp_utc": stamp,
                    "ticker": ticker,
                    "expiration": "2026-09-15",
                    "symbol": f"{ticker}260915C{contract:08d}",
                    "strike": 590.0 + contract,
                    "option_type": "call",
                    "bid": 1.0 if quoted else None,
                    "ask": 1.1 if quoted else None,
                    "underlying_spot": 600.05,
                }
            )
    # Written in one pass: the storage layer is exercised for correctness in
    # tests/test_storage.py, and per-cycle rewrites here would make the QA
    # fixtures quadratic.
    storage.append_underlying_snapshot(
        underlying_rows, data_dir=data_dir, ticker=ticker, day=DAY
    )
    storage.append_option_snapshot(option_rows, data_dir=data_dir, ticker=ticker, day=DAY)

    if write_metadata:
        tracker = ContractTracker(
            storage.metadata_path(data_dir, ticker, DAY), ticker=ticker, trading_date=DAY
        )
        tracker.load()
        tracker.add_contracts(
            [
                {"symbol": f"{ticker}260915C{i:08d}", "strike": 590.0 + i, "option_type": "call"}
                for i in range(contracts)
            ],
            expiration="2026-09-15",
            discovered_utc=OPEN,
        )


@pytest.fixture
def clock(settings: Settings) -> Any:
    return build_market_clock(settings)


def analyze(data_dir: Path, clock: Any, ticker: str = "SPY") -> qa_report.DayReport:
    return qa_report.analyze_day(data_dir, ticker, DAY, clock)


def test_complete_day(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406)
    report = analyze(tmp_path, clock)
    assert report.expected_cycles == 406
    assert report.observed_cycles == 406
    assert report.missing_cycles == 0
    assert report.coverage_pct == 100.0
    assert report.classification == qa_report.COMPLETE
    assert report.unique_symbols == 42
    assert report.median_rows_per_cycle == 42
    assert report.min_rows_per_cycle == 42
    assert report.two_sided_pct == 100.0
    assert report.contracts_discovered == 42
    assert report.largest_gap_minutes == 1.0
    assert report.first_observation_utc == OPEN.isoformat()


def test_missing_day(tmp_path: Path, clock: Any) -> None:
    report = analyze(tmp_path, clock)
    assert report.classification == qa_report.MISSING
    assert report.missing_cycles == 406
    assert report.coverage_pct == 0.0


def test_partial_day_is_flagged(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=200)
    report = analyze(tmp_path, clock)
    assert report.observed_cycles == 200
    assert report.classification == qa_report.PARTIAL
    assert report.coverage_pct == pytest.approx(49.26, abs=0.05)
    assert report.largest_gap_minutes == 206.0


def test_gap_in_the_middle_of_the_session(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406, skip_cycles=set(range(100, 130)))
    report = analyze(tmp_path, clock)
    assert report.missing_cycles == 30
    assert report.largest_gap_minutes == 31.0
    assert report.cycles_with_zero_option_rows == 30
    assert report.classification == qa_report.SUSPICIOUS


def test_all_null_quote_cycles_are_suspicious(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406, null_quote_cycles={5, 6, 7})
    report = analyze(tmp_path, clock)
    assert report.cycles_all_bid_ask_null == 3
    assert report.bid_availability_pct < 100.0
    assert report.classification == qa_report.SUSPICIOUS


def test_thin_cycle_is_suspicious(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406, thin_cycle=42)
    report = analyze(tmp_path, clock)
    assert report.min_rows_per_cycle == 2
    assert report.max_rows_per_cycle == 42
    assert report.classification == qa_report.SUSPICIOUS
    assert any("thin cycle" in note for note in report.notes)


def test_missing_metadata_is_suspicious(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406, write_metadata=False)
    report = analyze(tmp_path, clock)
    assert not report.metadata_file_present
    assert report.classification == qa_report.SUSPICIOUS


def test_underlying_metrics(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406)
    report = analyze(tmp_path, clock)
    assert report.underlying_expected_cycles == 406
    assert report.underlying_observed_cycles == 406
    assert report.underlying_coverage_pct == 100.0
    assert report.underlying_null_bid_ask_pct == 0.0


def test_missing_underlying_file_is_flagged(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406, write_underlying=False)
    report = analyze(tmp_path, clock)
    assert not report.underlying_file_present
    assert report.classification == qa_report.SUSPICIOUS


def test_non_trading_days_are_not_reported(tmp_path: Path, clock: Any) -> None:
    reports = qa_report.analyze_range(
        tmp_path, ["SPY"], date(2026, 9, 12), date(2026, 9, 13), clock
    )
    assert reports == []


def test_range_covers_every_session(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406)
    reports = qa_report.analyze_range(
        tmp_path, ["SPY", "QQQ"], date(2026, 9, 14), date(2026, 9, 15), clock
    )
    assert len(reports) == 4
    complete = [r for r in reports if r.classification == qa_report.COMPLETE]
    assert len(complete) == 1


def test_console_and_csv_rendering(tmp_path: Path, clock: Any) -> None:
    build_day(tmp_path, cycles=406)
    reports = qa_report.analyze_range(tmp_path, ["SPY"], DAY, DAY, clock)
    text = qa_report.render_console(reports)
    assert "2026-09-15" in text
    assert "COMPLETE" in text
    assert qa_report.render_console([]).startswith("No trading days")

    csv_path = tmp_path / "out" / "qa.csv"
    qa_report.write_csv(reports, csv_path)
    content = csv_path.read_text(encoding="utf-8")
    assert "coverage_pct" in content
    assert "SPY" in content


def test_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    build_day(tmp_path, cycles=406)
    monkeypatch.setattr(qa_report, "_settings_or_defaults", lambda env_file: settings)
    code = qa_report.main(
        [
            "--ticker",
            "SPY",
            "--start-date",
            "2026-09-15",
            "--end-date",
            "2026-09-15",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    bad = qa_report.main(
        [
            "--ticker",
            "QQQ",
            "--start-date",
            "2026-09-15",
            "--end-date",
            "2026-09-15",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert bad == 1
