"""Historical dataset validation for one ticker and trading date.

This script exists to prove, from the data alone, that the properties a
backtest depends on actually hold:

* contracts discovered in the morning keep receiving observations all session,
  including after they drift out of the current ATM window (sticky tracking),
* observations line up with the intended poll boundaries,
* no duplicate (cycle_id, symbol) rows exist,
* quote coverage is reasonable, and
* vendor greek timestamps are distinct from our poll timestamps, so nobody
  mistakes Tradier greeks for minute-resolution data.

    python validate_dataset.py --ticker SPY --date 2026-09-15 --data-dir ./data
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from config import ConfigError, Settings, load_settings
from tradier_collector.market_clock import build_market_clock
from tradier_collector.storage import (
    load_option_day,
    load_underlying_day,
    metadata_path,
)

MORNING_WINDOW_MINUTES = 30
SAMPLE_CONTRACTS = 5


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationReport:
    ticker: str
    trading_date: str
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail))

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    def render(self) -> str:
        lines = [
            f"Dataset validation: {self.ticker} {self.trading_date}",
            "=" * 60,
        ]
        for check in self.checks:
            mark = "PASS" if check.passed else "FAIL"
            lines.append(f"[{mark}] {check.name}")
            for line in check.detail.splitlines():
                lines.append(f"       {line}")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines.extend(f"  - {note}" for note in self.notes)
        lines.append("")
        lines.append("RESULT: " + ("ALL CHECKS PASSED" if self.ok else "CHECKS FAILED"))
        return "\n".join(lines)


def _load_metadata(data_dir: Path, ticker: str, day: date) -> list[dict[str, object]]:
    path = metadata_path(data_dir, ticker, day)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    contracts = payload.get("contracts") if isinstance(payload, dict) else None
    return contracts if isinstance(contracts, list) else []


def validate(
    data_dir: Path,
    ticker: str,
    day: date,
    settings: Settings,
) -> ValidationReport:
    report = ValidationReport(ticker=ticker, trading_date=day.isoformat())
    clock = build_market_clock(settings)

    options = load_option_day(data_dir, ticker, day)
    underlying = load_underlying_day(data_dir, ticker, day)
    contracts = _load_metadata(data_dir, ticker, day)

    report.add(
        "option observations present",
        not options.empty,
        f"{len(options)} option rows loaded",
    )
    report.add(
        "underlying observations present",
        not underlying.empty,
        f"{len(underlying)} underlying rows loaded",
    )
    report.add(
        "contract metadata present",
        bool(contracts),
        f"{len(contracts)} contracts in the sticky universe",
    )
    if options.empty or not contracts:
        report.notes.append("further checks skipped: no options data or metadata")
        return report

    options = options.copy()
    options["poll_timestamp_utc"] = pd.to_datetime(options["poll_timestamp_utc"], utc=True)
    session_start = options["poll_timestamp_utc"].min()
    session_end = options["poll_timestamp_utc"].max()

    # ---------------------------------------------------- sticky tracking
    morning_cutoff = session_start + timedelta(minutes=MORNING_WINDOW_MINUTES)
    morning = [
        c
        for c in contracts
        if isinstance(c.get("first_discovered_utc"), str)
        and pd.Timestamp(str(c["first_discovered_utc"])) <= morning_cutoff
    ]
    sample = morning[:: max(1, len(morning) // SAMPLE_CONTRACTS)][:SAMPLE_CONTRACTS]
    late_spot = float(underlying["last"].dropna().iloc[-1]) if not underlying.empty else None

    detail_lines: list[str] = []
    sticky_ok = bool(sample)
    for contract in sample:
        symbol = str(contract.get("symbol"))
        rows = options[options["symbol"] == symbol]
        if rows.empty:
            detail_lines.append(f"{symbol}: NO observations at all")
            sticky_ok = False
            continue
        last_seen = rows["poll_timestamp_utc"].max()
        minutes_to_end = (session_end - last_seen).total_seconds() / 60.0
        strike = contract.get("strike")
        drift = (
            f", strike {strike} vs late spot {late_spot:.2f}"
            if late_spot is not None and isinstance(strike, int | float)
            else ""
        )
        detail_lines.append(
            f"{symbol}: {len(rows)} observations, last {last_seen.isoformat()} "
            f"({minutes_to_end:.0f} min before the final cycle){drift}"
        )
        if minutes_to_end > 5:
            sticky_ok = False
    report.add(
        "sticky contracts keep reporting all session",
        sticky_ok,
        "\n".join(detail_lines) or "no morning contracts found to sample",
    )

    # ------------------------------------------- contracts outside the window
    if late_spot is not None:
        strikes = sorted({float(s) for s in options["strike"].dropna().unique()})
        if strikes:
            spacing = min(
                (b - a for a, b in zip(strikes, strikes[1:], strict=False) if b > a),
                default=1.0,
            )
            window_half_width = spacing * settings.discovery_strikes_each_side
            far = options[(options["strike"] - late_spot).abs() > window_half_width]
            late_far = far[far["poll_timestamp_utc"] > session_end - timedelta(minutes=30)]
            report.add(
                "out-of-window contracts still collected late in the session",
                not late_far.empty,
                f"{len(late_far)} observations in the final 30 minutes for contracts more "
                f"than {window_half_width:g} points from spot ({late_spot:.2f}); these are "
                f"only in the dataset because the universe is sticky",
            )

    # ---------------------------------------------------- cycle alignment
    expected = set(clock.expected_poll_timestamps(day))
    observed = {ts.to_pydatetime() for ts in options["poll_timestamp_utc"].unique()}
    unexpected = sorted(observed - expected)
    report.add(
        "observations align with intended poll boundaries",
        not unexpected,
        f"{len(observed)} distinct cycles; {len(unexpected)} outside the expected boundaries"
        + (f" (first: {unexpected[0].isoformat()})" if unexpected else ""),
    )

    per_cycle_stamp = options.groupby("cycle_id")["poll_timestamp_utc"].nunique()
    report.add(
        "one poll timestamp per cycle_id",
        bool((per_cycle_stamp == 1).all()),
        f"{int((per_cycle_stamp != 1).sum())} cycle_ids map to more than one timestamp",
    )

    # -------------------------------------------------------- duplicates
    duplicates = int(options.duplicated(subset=["cycle_id", "symbol"]).sum())
    report.add(
        "no duplicate cycle_id + symbol rows",
        duplicates == 0,
        f"{duplicates} duplicate rows",
    )

    # ----------------------------------------------------- quote coverage
    total = len(options)
    bid_pct = 100.0 * options["bid"].notna().sum() / total
    ask_pct = 100.0 * options["ask"].notna().sum() / total
    two_sided = 100.0 * (options["bid"].notna() & options["ask"].notna()).sum() / total
    report.add(
        "quote coverage recorded",
        total > 0,
        f"bid {bid_pct:.2f}%, ask {ask_pct:.2f}%, two-sided {two_sided:.2f}%",
    )
    if two_sided < 50:
        report.notes.append(
            "two-sided coverage below 50%: expected for deep out-of-the-money 0DTE "
            "strikes late in the day, but worth eyeballing"
        )

    # ------------------------------------------ vendor greeks are not ours
    greeks = options["tradier_greeks_updated_at"].dropna()
    if greeks.empty:
        report.add(
            "vendor greek timestamps distinct from poll timestamps",
            True,
            "no vendor greek timestamps present (Tradier omitted greeks); nothing to confuse",
        )
    else:
        greek_times = pd.to_datetime(greeks, utc=True)
        matching = options.loc[greek_times.index, "poll_timestamp_utc"]
        identical = int((greek_times == matching).sum())
        report.add(
            "vendor greek timestamps distinct from poll timestamps",
            identical == 0,
            f"{len(greek_times)} greek timestamps, {identical} identical to the poll timestamp; "
            f"distinct values: {greek_times.nunique()}",
        )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate one collected trading day")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--env-file", default=".env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigError:
        settings = load_settings({"TRADIER_API_TOKEN": "validate-offline"}, env_file=None)

    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir
    day = datetime.strptime(args.date, "%Y-%m-%d").date()
    report = validate(data_dir, args.ticker.upper(), day, settings)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
