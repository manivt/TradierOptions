"""Daily QA report over the collected dataset.

For every expected XNYS session in a date range this checks the three
artefacts (options Parquet, underlying Parquet, contract metadata) and
compares observations against the *explicit* list of expected poll
timestamps produced by the market clock.  Approximating the expected count
would hide exactly the failures this report exists to find.

Example
-------
    python qa_report.py --ticker SPY --start-date 2026-09-01 \
        --end-date 2026-09-15 --data-dir ./data --csv qa.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import ConfigError, Settings, load_settings
from tradier_collector.market_clock import MarketClock, build_market_clock
from tradier_collector.storage import (
    load_option_day,
    load_underlying_day,
    metadata_path,
    option_path,
    underlying_path,
)

logger = logging.getLogger(__name__)

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
MISSING = "MISSING"
SUSPICIOUS = "SUSPICIOUS"


@dataclass
class DayReport:
    ticker: str
    trading_date: str
    classification: str = MISSING

    options_file_present: bool = False
    underlying_file_present: bool = False
    metadata_file_present: bool = False

    expected_cycles: int = 0
    observed_cycles: int = 0
    missing_cycles: int = 0
    coverage_pct: float = 0.0
    largest_gap_minutes: float = 0.0
    unexpected_cycles: int = 0
    first_observation_utc: str | None = None
    last_observation_utc: str | None = None

    contracts_discovered: int = 0
    unique_symbols: int = 0
    median_rows_per_cycle: float = 0.0
    min_rows_per_cycle: int = 0
    max_rows_per_cycle: int = 0
    duplicate_rows: int = 0

    bid_availability_pct: float = 0.0
    ask_availability_pct: float = 0.0
    two_sided_pct: float = 0.0
    cycles_with_zero_option_rows: int = 0
    cycles_all_bid_ask_null: int = 0

    underlying_expected_cycles: int = 0
    underlying_observed_cycles: int = 0
    underlying_coverage_pct: float = 0.0
    underlying_null_bid_ask_pct: float = 0.0

    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["notes"] = "; ".join(self.notes)
        return row


def _pct(numerator: float, denominator: float) -> float:
    return round(100.0 * numerator / denominator, 3) if denominator else 0.0


def _largest_gap_minutes(observed: list[datetime], expected: list[datetime]) -> float:
    """Largest gap between consecutive observations, session edges included."""
    if not expected:
        return 0.0
    if not observed:
        return round((expected[-1] - expected[0]).total_seconds() / 60.0, 2)
    edges = [expected[0], *sorted(observed), expected[-1]]
    gap = max((b - a).total_seconds() for a, b in zip(edges, edges[1:], strict=False))
    return round(gap / 60.0, 2)


def analyze_day(
    data_dir: Path,
    ticker: str,
    day: date,
    clock: MarketClock,
    *,
    warning_coverage_pct: float = 90.0,
    healthy_coverage_pct: float = 98.0,
) -> DayReport:
    """Build the QA report for one ticker on one trading date."""
    report = DayReport(ticker=ticker, trading_date=day.isoformat())
    expected = clock.expected_poll_timestamps(day)
    report.expected_cycles = len(expected)
    report.underlying_expected_cycles = len(expected)

    report.options_file_present = option_path(data_dir, ticker, day).exists()
    report.underlying_file_present = underlying_path(data_dir, ticker, day).exists()
    report.metadata_file_present = metadata_path(data_dir, ticker, day).exists()

    if not report.options_file_present and not report.underlying_file_present:
        report.classification = MISSING
        report.missing_cycles = report.expected_cycles
        report.notes.append("no options or underlying file for this session")
        return report

    options = load_option_day(data_dir, ticker, day)
    underlying = load_underlying_day(data_dir, ticker, day)

    expected_set = {ts.replace(microsecond=0) for ts in expected}
    observed_stamps: list[datetime] = []
    if not options.empty:
        stamps = pd.to_datetime(options["poll_timestamp_utc"], utc=True)
        observed_stamps = sorted({ts.to_pydatetime().replace(microsecond=0) for ts in stamps})
    observed_set = set(observed_stamps)

    report.observed_cycles = len(observed_set & expected_set)
    report.unexpected_cycles = len(observed_set - expected_set)
    report.missing_cycles = len(expected_set - observed_set)
    report.coverage_pct = _pct(report.observed_cycles, report.expected_cycles)
    report.largest_gap_minutes = _largest_gap_minutes(observed_stamps, expected)
    if observed_stamps:
        report.first_observation_utc = observed_stamps[0].isoformat()
        report.last_observation_utc = observed_stamps[-1].isoformat()

    if not options.empty:
        report.unique_symbols = int(options["symbol"].nunique())
        per_cycle = options.groupby("cycle_id").size()
        report.median_rows_per_cycle = float(statistics.median(per_cycle.tolist()))
        report.min_rows_per_cycle = int(per_cycle.min())
        report.max_rows_per_cycle = int(per_cycle.max())

        duplicates = options.duplicated(subset=["cycle_id", "symbol"]).sum()
        report.duplicate_rows = int(duplicates)

        total_rows = len(options)
        has_bid = options["bid"].notna()
        has_ask = options["ask"].notna()
        report.bid_availability_pct = _pct(int(has_bid.sum()), total_rows)
        report.ask_availability_pct = _pct(int(has_ask.sum()), total_rows)
        report.two_sided_pct = _pct(int((has_bid & has_ask).sum()), total_rows)

        quotes_by_cycle = options.assign(_two_sided=(has_bid | has_ask)).groupby("cycle_id")[
            "_two_sided"
        ].sum()
        report.cycles_all_bid_ask_null = int((quotes_by_cycle == 0).sum())

    # A cycle present in the underlying file but absent from the options file is
    # a cycle with zero option rows.
    if not underlying.empty:
        under_stamps = pd.to_datetime(underlying["poll_timestamp_utc"], utc=True)
        under_set = {ts.to_pydatetime().replace(microsecond=0) for ts in under_stamps}
        report.underlying_observed_cycles = len(under_set & expected_set)
        report.underlying_coverage_pct = _pct(
            report.underlying_observed_cycles, report.underlying_expected_cycles
        )
        null_quotes = underlying["bid"].isna() | underlying["ask"].isna()
        report.underlying_null_bid_ask_pct = _pct(int(null_quotes.sum()), len(underlying))
        report.cycles_with_zero_option_rows = len(under_set - observed_set)

    if report.metadata_file_present:
        try:
            payload = json.loads(
                metadata_path(data_dir, ticker, day).read_text(encoding="utf-8")
            )
            contracts = payload.get("contracts", []) if isinstance(payload, dict) else []
            report.contracts_discovered = len(contracts)
        except (OSError, ValueError) as exc:
            report.notes.append(f"contract metadata unreadable: {exc}")
    else:
        report.notes.append("contract metadata file missing")

    report.classification = _classify(
        report, warning_coverage_pct=warning_coverage_pct, healthy_coverage_pct=healthy_coverage_pct
    )
    return report


def _classify(
    report: DayReport, *, warning_coverage_pct: float, healthy_coverage_pct: float
) -> str:
    """Classify a day as COMPLETE, PARTIAL, MISSING or SUSPICIOUS."""
    if report.observed_cycles == 0 and report.underlying_observed_cycles == 0:
        report.notes.append("no observations at all")
        return MISSING
    if report.coverage_pct < warning_coverage_pct:
        report.notes.append(f"option coverage {report.coverage_pct:.2f}% below warning threshold")
        return PARTIAL

    suspicious = False
    if report.duplicate_rows:
        report.notes.append(f"{report.duplicate_rows} duplicate cycle+symbol rows")
        suspicious = True
    if report.cycles_all_bid_ask_null:
        report.notes.append(
            f"{report.cycles_all_bid_ask_null} cycles where every contract had a null bid and ask"
        )
        suspicious = True
    if report.cycles_with_zero_option_rows:
        report.notes.append(
            f"{report.cycles_with_zero_option_rows} cycles with an underlying quote but no options"
        )
        suspicious = True
    if report.unexpected_cycles:
        report.notes.append(
            f"{report.unexpected_cycles} observations outside the expected boundaries"
        )
        suspicious = True
    if (
        report.min_rows_per_cycle
        and report.median_rows_per_cycle
        and report.min_rows_per_cycle < 0.5 * report.median_rows_per_cycle
    ):
            report.notes.append(
                f"thin cycle: min {report.min_rows_per_cycle} rows vs median "
                f"{report.median_rows_per_cycle:.0f}"
            )
            suspicious = True
    if not report.metadata_file_present or report.contracts_discovered == 0:
        suspicious = True
    if report.underlying_coverage_pct < warning_coverage_pct:
        report.notes.append(
            f"underlying coverage {report.underlying_coverage_pct:.2f}% below warning threshold"
        )
        suspicious = True

    if suspicious:
        return SUSPICIOUS
    if report.coverage_pct < healthy_coverage_pct:
        report.notes.append(
            f"option coverage {report.coverage_pct:.2f}% below healthy threshold"
        )
        return PARTIAL
    return COMPLETE


def analyze_range(
    data_dir: Path,
    tickers: list[str],
    start: date,
    end: date,
    clock: MarketClock,
    *,
    warning_coverage_pct: float = 90.0,
    healthy_coverage_pct: float = 98.0,
) -> list[DayReport]:
    reports: list[DayReport] = []
    for day in clock.trading_days(start, end):
        for ticker in tickers:
            reports.append(
                analyze_day(
                    data_dir,
                    ticker,
                    day,
                    clock,
                    warning_coverage_pct=warning_coverage_pct,
                    healthy_coverage_pct=healthy_coverage_pct,
                )
            )
    return reports


def render_console(reports: list[DayReport]) -> str:
    """A readable console report."""
    if not reports:
        return "No trading days in the requested range."
    lines: list[str] = []
    header = (
        f"{'DATE':<12}{'TICKER':<8}{'CLASS':<12}{'COV%':>8}{'OBS':>7}{'EXP':>7}"
        f"{'GAPmin':>8}{'SYMS':>7}{'MEDROW':>8}{'2SIDE%':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for report in reports:
        lines.append(
            f"{report.trading_date:<12}{report.ticker:<8}{report.classification:<12}"
            f"{report.coverage_pct:>8.2f}{report.observed_cycles:>7}{report.expected_cycles:>7}"
            f"{report.largest_gap_minutes:>8.1f}{report.unique_symbols:>7}"
            f"{report.median_rows_per_cycle:>8.0f}{report.two_sided_pct:>8.2f}"
        )
    lines.append("")

    counts: dict[str, int] = {}
    for report in reports:
        counts[report.classification] = counts.get(report.classification, 0) + 1
    lines.append("Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    flagged = [r for r in reports if r.classification != COMPLETE]
    if flagged:
        lines.append("")
        lines.append("Findings:")
        for report in flagged:
            detail = "; ".join(report.notes) or "see metrics above"
            lines.append(
                f"  {report.trading_date} {report.ticker} "
                f"[{report.classification}]: {detail}"
            )
    return "\n".join(lines)


def write_csv(reports: list[DayReport], path: Path) -> None:
    rows = [r.as_row() for r in reports]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["ticker"])
        writer.writeheader()
        writer.writerows(rows)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA report for collected 0DTE data")
    parser.add_argument("--ticker", action="append", help="Ticker (repeatable, or comma separated)")
    parser.add_argument("--start-date", required=True, type=_parse_date)
    parser.add_argument("--end-date", required=True, type=_parse_date)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--csv", default=None, help="Write a CSV summary to this path")
    parser.add_argument("--env-file", default=".env")
    return parser.parse_args(argv)


def _settings_or_defaults(env_file: str) -> Settings:
    """QA must work without a token, so fall back to defaults when absent."""
    try:
        return load_settings(env_file=env_file)
    except ConfigError:
        return load_settings({"TRADIER_API_TOKEN": "qa-offline"}, env_file=None)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    settings = _settings_or_defaults(args.env_file)

    tickers: list[str] = []
    for raw in args.ticker or []:
        tickers.extend(t.strip().upper() for t in raw.split(",") if t.strip())
    if not tickers:
        tickers = list(settings.tickers)

    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir
    clock = build_market_clock(settings)
    reports = analyze_range(
        data_dir,
        tickers,
        args.start_date,
        args.end_date,
        clock,
        warning_coverage_pct=settings.warning_coverage_pct,
        healthy_coverage_pct=settings.healthy_coverage_pct,
    )
    print(render_console(reports))
    if args.csv:
        write_csv(reports, Path(args.csv))
        print(f"\nCSV summary written to {args.csv}")
    return 0 if all(r.classification == COMPLETE for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
