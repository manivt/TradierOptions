from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import watchdog
from config import Settings

DAY = date(2026, 9, 15)
MIDDAY = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)
AFTER_CLOSE = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)


def write_health(
    data_dir: Path,
    *,
    coverage: float = 100.0,
    finished: bool = True,
    last_cycle: datetime | None = MIDDAY,
    errors: int = 0,
    status: str = "healthy",
    day: date = DAY,
    tickers: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "date": day.isoformat(),
        "collector_started_utc": "2026-09-15T13:30:00+00:00",
        "collector_finished_utc": "2026-09-15T20:15:00+00:00" if finished else None,
        "expected_cycles": 406,
        "tickers": tickers
        if tickers is not None
        else {
            "SPY": {
                "expected_cycles": 406,
                "observed_cycles": int(406 * coverage / 100),
                "coverage_pct": coverage,
                "errors": errors,
                "last_successful_cycle": last_cycle.isoformat() if last_cycle else None,
            }
        },
        "overall_error_count": errors,
        "status": status,
    }
    path = data_dir / "health" / f"{day.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_healthy_finished_session(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path)
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_HEALTHY
    assert result.status == "healthy"


def test_missing_health_file_is_fatal(settings: Settings, tmp_path: Path) -> None:
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL
    assert "health file missing" in result.messages[0]


def test_unfinished_session_is_fatal_after_the_close(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, finished=False)
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL
    assert any("finish time" in m for m in result.messages)


def test_coverage_between_thresholds_is_a_warning(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, coverage=94.0, status="warning")
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_WARNING


def test_coverage_below_the_warning_threshold_is_fatal(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, coverage=40.0, status="failed")
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL


def test_stale_last_cycle_during_the_session_is_fatal(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, finished=False, last_cycle=MIDDAY - timedelta(minutes=45))
    result = watchdog.check_day(
        settings, DAY, now=MIDDAY, data_dir=tmp_path, max_silence_minutes=10
    )
    assert result.exit_code == watchdog.EXIT_FATAL
    assert any("minutes ago" in m for m in result.messages)


def test_recent_cycle_during_the_session_is_healthy(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, finished=False, last_cycle=MIDDAY - timedelta(minutes=2))
    result = watchdog.check_day(settings, DAY, now=MIDDAY, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_HEALTHY


def test_no_successful_cycle_yet_during_the_session(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, finished=False, last_cycle=None)
    result = watchdog.check_day(settings, DAY, now=MIDDAY, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL


def test_errors_raise_a_warning(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, errors=3)
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_WARNING


def test_non_trading_day_is_healthy(settings: Settings, tmp_path: Path) -> None:
    result = watchdog.check_day(settings, date(2026, 9, 12), now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_HEALTHY


def test_before_the_open_is_healthy(settings: Settings, tmp_path: Path) -> None:
    result = watchdog.check_day(
        settings, DAY, now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC), data_dir=tmp_path
    )
    assert result.exit_code == watchdog.EXIT_HEALTHY


def test_corrupt_health_file_is_fatal(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "health" / f"{DAY.isoformat()}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL


def test_health_file_without_tickers_is_fatal(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path, tickers={})
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert result.exit_code == watchdog.EXIT_FATAL


def test_render_includes_the_status(settings: Settings, tmp_path: Path) -> None:
    write_health(tmp_path)
    result = watchdog.check_day(settings, DAY, now=AFTER_CLOSE, data_dir=tmp_path)
    assert "WATCHDOG HEALTHY" in result.render()
