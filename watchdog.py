"""External watchdog for silent collector failures.

Run this as a *separate* process (cron or a systemd timer).  A collector that
has crashed, hung, or quietly stopped producing data cannot report its own
failure, so the check deliberately reads only the artefacts on disk.

Checks
------
1. The daily health file exists for the trading day being checked.
2. The collector recorded a finish time (only enforced after the session end).
3. The last successful cycle is recent enough while the session is running.
4. Coverage is at or above the configured thresholds.

Exit codes
----------
0  healthy
1  warning
2  fatal or missing
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import ConfigError, Settings, load_settings
from tradier_collector.market_clock import build_market_clock
from tradier_collector.storage import health_path

EXIT_HEALTHY = 0
EXIT_WARNING = 1
EXIT_FATAL = 2


@dataclass
class WatchdogResult:
    status: str
    exit_code: int
    messages: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"WATCHDOG {self.status.upper()} (exit {self.exit_code})"
        return "\n".join([head, *(f"  - {m}" for m in self.messages)])


def check_day(
    settings: Settings,
    day: date,
    *,
    now: datetime | None = None,
    max_silence_minutes: int = 10,
    data_dir: Path | None = None,
) -> WatchdogResult:
    """Evaluate the collector state for one trading date."""
    now = now or datetime.now(tz=UTC)
    root = Path(data_dir or settings.data_dir)
    clock = build_market_clock(settings)
    messages: list[str] = []

    if not clock.is_trading_day(day):
        return WatchdogResult(
            status="healthy",
            exit_code=EXIT_HEALTHY,
            messages=[f"{day.isoformat()} is not an XNYS session; nothing to check"],
        )

    window = clock.session_window(day)
    session_finished = window is not None and now > window.end_utc
    session_running = window is not None and window.contains(now)
    if window is not None and now < window.start_utc:
        return WatchdogResult(
            status="healthy",
            exit_code=EXIT_HEALTHY,
            messages=[f"session {day.isoformat()} has not started yet"],
        )

    path = health_path(root, day)
    if not path.exists():
        return WatchdogResult(
            status="fatal",
            exit_code=EXIT_FATAL,
            messages=[f"health file missing: {path}"],
        )

    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return WatchdogResult(
            status="fatal", exit_code=EXIT_FATAL, messages=[f"health file unreadable: {exc}"]
        )
    if not isinstance(payload, dict):
        return WatchdogResult(
            status="fatal", exit_code=EXIT_FATAL, messages=["health file is not a JSON object"]
        )

    worst = EXIT_HEALTHY

    if session_finished and not payload.get("collector_finished_utc"):
        messages.append("collector never recorded a finish time for a completed session")
        worst = max(worst, EXIT_FATAL)

    tickers = payload.get("tickers")
    if not isinstance(tickers, dict) or not tickers:
        return WatchdogResult(
            status="fatal", exit_code=EXIT_FATAL, messages=["health file has no ticker section"]
        )

    for ticker, raw in sorted(tickers.items()):
        if not isinstance(raw, dict):
            messages.append(f"{ticker}: malformed health entry")
            worst = max(worst, EXIT_FATAL)
            continue

        coverage = float(raw.get("coverage_pct", 0.0) or 0.0)
        if session_finished:
            if coverage < settings.warning_coverage_pct:
                messages.append(
                    f"{ticker}: coverage {coverage:.2f}% is below the warning threshold"
                )
                worst = max(worst, EXIT_FATAL)
            elif coverage < settings.healthy_coverage_pct:
                messages.append(
                    f"{ticker}: coverage {coverage:.2f}% is below the healthy threshold"
                )
                worst = max(worst, EXIT_WARNING)
            else:
                messages.append(f"{ticker}: coverage {coverage:.2f}%")

        last_cycle = raw.get("last_successful_cycle")
        if session_running:
            if not isinstance(last_cycle, str):
                messages.append(f"{ticker}: no successful cycle recorded yet today")
                worst = max(worst, EXIT_FATAL)
            else:
                try:
                    last = datetime.fromisoformat(last_cycle)
                except ValueError:
                    messages.append(f"{ticker}: unparseable last_successful_cycle {last_cycle!r}")
                    worst = max(worst, EXIT_FATAL)
                else:
                    silence = now - last
                    if silence > timedelta(minutes=max_silence_minutes):
                        messages.append(
                            f"{ticker}: last successful cycle was "
                            f"{silence.total_seconds() / 60.0:.1f} minutes ago"
                        )
                        worst = max(worst, EXIT_FATAL)
                    else:
                        messages.append(
                            f"{ticker}: last cycle {silence.total_seconds() / 60.0:.1f} minutes ago"
                        )

        errors = int(raw.get("errors", 0) or 0)
        if errors:
            messages.append(f"{ticker}: {errors} cycle errors recorded")
            worst = max(worst, EXIT_WARNING)

    reported = payload.get("status")
    if reported == "failed":
        messages.append("collector reported status=failed")
        worst = max(worst, EXIT_FATAL)
    elif reported == "warning":
        worst = max(worst, EXIT_WARNING)

    status = {EXIT_HEALTHY: "healthy", EXIT_WARNING: "warning", EXIT_FATAL: "fatal"}[worst]
    return WatchdogResult(status=status, exit_code=worst, messages=messages)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watchdog for the Tradier 0DTE collector")
    parser.add_argument("--date", default=None, help="Trading date (default: today in market tz)")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--max-silence-minutes",
        type=int,
        default=10,
        help="How long a running session may go without a successful cycle",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigError:
        settings = load_settings({"TRADIER_API_TOKEN": "watchdog-offline"}, env_file=None)

    clock = build_market_clock(settings)
    day = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else clock.today(datetime.now(tz=UTC))
    )
    result = check_day(
        settings,
        day,
        max_silence_minutes=args.max_silence_minutes,
        data_dir=Path(args.data_dir) if args.data_dir else None,
    )
    print(result.render())
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
