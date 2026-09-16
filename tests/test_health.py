from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tradier_collector.collector import TickerResult
from tradier_collector.cycle import CycleResult
from tradier_collector.health import (
    STATUS_FAILED,
    STATUS_HEALTHY,
    STATUS_WARNING,
    HealthTracker,
    classify_status,
)

DAY = date(2026, 9, 15)
START = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


def make_result(index: int, *, ok: bool = True, error: str | None = None) -> CycleResult:
    stamp = START + timedelta(minutes=index)
    return CycleResult(
        cycle_id=f"cyc-{index}",
        poll_timestamp_utc=stamp,
        trading_date=DAY,
        results=[
            TickerResult(
                ticker="SPY",
                success=ok,
                underlying_saved=1,
                new_contracts_discovered=42 if index == 0 else 0,
                tracked_contract_count=42,
                option_rows_saved=42 if ok else 0,
                error=error,
            )
        ],
    )


def make_tracker(tmp_path: Path, expected: int = 10) -> HealthTracker:
    return HealthTracker(
        trading_date=DAY,
        tickers=("SPY",),
        expected_cycles=expected,
        data_dir=tmp_path,
        session_start_utc=START,
        session_end_utc=START + timedelta(minutes=expected - 1),
    )


def test_classify_status_thresholds() -> None:
    assert classify_status(99.0, 98.0, 90.0) == STATUS_HEALTHY
    assert classify_status(98.0, 98.0, 90.0) == STATUS_HEALTHY
    assert classify_status(95.0, 98.0, 90.0) == STATUS_WARNING
    assert classify_status(50.0, 98.0, 90.0) == STATUS_FAILED


def test_coverage_and_status(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    for index in range(10):
        tracker.record_cycle(make_result(index))
    assert tracker.tickers["SPY"].coverage_pct == 100.0
    assert tracker.status() == STATUS_HEALTHY
    assert tracker.overall_error_count == 0


def test_partial_coverage_is_a_warning(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path, expected=100)
    for index in range(95):
        tracker.record_cycle(make_result(index))
    assert tracker.tickers["SPY"].coverage_pct == 95.0
    assert tracker.status() == STATUS_WARNING


def test_errors_are_counted_and_do_not_count_as_observations(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    tracker.record_cycle(make_result(0))
    tracker.record_cycle(make_result(1, ok=False, error="boom"))
    assert tracker.tickers["SPY"].observed_cycles == 1
    assert tracker.tickers["SPY"].errors == 1
    assert tracker.tickers["SPY"].last_error == "boom"
    assert tracker.overall_error_count == 1


def test_largest_gap_includes_the_session_edges(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path, expected=10)
    tracker.record_cycle(make_result(0))
    tracker.record_cycle(make_result(5))
    tracker.finished_utc = datetime.now(tz=UTC)
    health = tracker.tickers["SPY"]
    assert health.largest_gap_minutes(START, START + timedelta(minutes=9)) == 5.0


def test_health_file_contents(tmp_path: Path) -> None:
    tracker = make_tracker(tmp_path)
    for index in range(10):
        tracker.record_cycle(make_result(index))
    path = tracker.write(finished=True)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "health" / "2026-09-15.json"
    assert payload["date"] == "2026-09-15"
    assert payload["collector_started_utc"]
    assert payload["collector_finished_utc"]
    assert payload["status"] == STATUS_HEALTHY
    assert payload["overall_error_count"] == 0
    spy = payload["tickers"]["SPY"]
    assert spy["expected_cycles"] == 10
    assert spy["observed_cycles"] == 10
    assert spy["coverage_pct"] == 100.0
    assert spy["contracts_discovered"] == 42
    assert spy["last_successful_cycle"].startswith("2026-09-15T13:39")
    assert "largest_gap_minutes" in spy


def test_restore_carries_counters_across_a_restart(tmp_path: Path) -> None:
    first = make_tracker(tmp_path)
    for index in range(4):
        first.record_cycle(make_result(index))
    first.write()

    second = make_tracker(tmp_path)
    assert second.restore() is True
    assert second.tickers["SPY"].observed_cycles == 4
    assert len(second.tickers["SPY"].observed_timestamps) == 4
    second.record_cycle(make_result(4))
    assert second.tickers["SPY"].observed_cycles == 5


def test_restore_ignores_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "health"
    path.mkdir(parents=True)
    (path / "2026-09-15.json").write_text("{broken", encoding="utf-8")
    tracker = make_tracker(tmp_path)
    assert tracker.restore() is False
    assert tracker.tickers["SPY"].observed_cycles == 0


def test_restore_ignores_a_file_for_another_day(tmp_path: Path) -> None:
    path = tmp_path / "health"
    path.mkdir(parents=True)
    (path / "2026-09-15.json").write_text(json.dumps({"date": "2026-09-14"}), encoding="utf-8")
    assert make_tracker(tmp_path).restore() is False


def test_restore_returns_false_without_a_file(tmp_path: Path) -> None:
    assert make_tracker(tmp_path).restore() is False
