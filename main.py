"""Entry point for the Tradier 0DTE collector.

Usage
-----
    python main.py                 # run until stopped (systemd-friendly)
    python main.py --max-cycles 3  # bounded run, useful for a first smoke run
    python main.py --once          # one cycle right now, ignoring market hours
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from config import ConfigError, Settings, load_settings
from scheduler import CollectorScheduler
from tradier_collector.logging_setup import setup_logging
from tradier_collector.storage import ensure_layout

logger = logging.getLogger("tradier_collector.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tradier 0DTE options data collector")
    parser.add_argument("--env-file", default=".env", help="Path to the .env file")
    parser.add_argument(
        "--max-cycles", type=int, default=None, help="Stop after this many cycles"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one cycle immediately, ignoring the session window",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default INFO)")
    return parser.parse_args(argv)


def run_single_cycle(settings: Settings) -> int:
    """One immediate cycle, for manual verification outside market hours."""
    scheduler = CollectorScheduler(settings)
    now = datetime.now(tz=UTC)
    trading_date = scheduler.clock.today(now)
    ensure_layout(settings.data_dir, list(settings.tickers))
    if not scheduler.clock.is_trading_day(trading_date):
        logger.warning(
            "%s is not an XNYS session; quotes will be stale and there is very "
            "likely no 0DTE expiration",
            trading_date.isoformat(),
        )
    result = scheduler.run_immediate_cycle(now)
    return 0 if not result.failed_tickers else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        settings.log_dir,
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        retention_days=settings.log_retention_days,
    )

    try:
        if args.once:
            return run_single_cycle(settings)
        CollectorScheduler(settings).run(max_cycles=args.max_cycles)
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        logger.info("Interrupted; shutting down")
        return 0
    except Exception:
        logger.exception("Collector terminated with an unhandled exception")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
