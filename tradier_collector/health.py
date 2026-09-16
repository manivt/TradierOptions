"""Daily health accounting and the data/health/YYYY-MM-DD.json artefact.

The health file is the collector view of its own session.  It is written
periodically during the day (so an external watchdog can see progress and the
last successful cycle) and finalised at the end of the session.

Coverage is measured against the explicit list of expected poll timestamps
produced by :mod:`tradier_collector.market_clock`, not an approximation.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .cycle import CycleResult

logger = logging.getLogger(__name__)

STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"


def classify_status(coverage_pct: float, healthy_pct: float, warning_pct: float) -> str:
    if coverage_pct >= healthy_pct:
        return STATUS_HEALTHY
    if coverage_pct >= warning_pct:
        return STATUS_WARNING
    return STATUS_FAILED


@dataclass
class TickerHealth:
    ticker: str
    expected_cycles: int = 0
    observed_cycles: int = 0
    contracts_discovered: int = 0
    tracked_contract_count: int = 0
    option_rows_saved: int = 0
    errors: int = 0
    batch_failures: int = 0
    skipped_cycles: int = 0
    last_error: str | None = None
    last_successful_cycle_utc: str | None = None
    observed_timestamps: list[datetime] = field(default_factory=list, repr=False)

    @property
    def coverage_pct(self) -> float:
        if self.expected_cycles <= 0:
            return 0.0
        return round(100.0 * self.observed_cycles / self.expected_cycles, 3)

    def largest_gap_minutes(
        self, window_start: datetime | None, window_end: datetime | None
    ) -> float:
        """Largest gap between consecutive observations, in minutes.

        The session boundaries are included so a late start or an early stop is
        counted as a gap rather than hidden.
        """
        stamps = sorted(self.observed_timestamps)
        if not stamps:
            if window_start and window_end:
                return round((window_end - window_start).total_seconds() / 60.0, 2)
            return 0.0
        edges = list(stamps)
        if window_start is not None:
            edges.insert(0, window_start)
        if window_end is not None:
            edges.append(window_end)
        gap = max(
            ((b - a).total_seconds() for a, b in zip(edges, edges[1:], strict=False)),
            default=0.0,
        )
        return round(gap / 60.0, 2)


class HealthTracker:
    """Accumulates per-ticker health for one trading day."""

    def __init__(
        self,
        *,
        trading_date: date,
        tickers: tuple[str, ...],
        expected_cycles: int,
        data_dir: Path | str,
        healthy_pct: float = 98.0,
        warning_pct: float = 90.0,
        session_start_utc: datetime | None = None,
        session_end_utc: datetime | None = None,
        started_utc: datetime | None = None,
    ) -> None:
        self.trading_date = trading_date
        self.data_dir = Path(data_dir)
        self.healthy_pct = healthy_pct
        self.warning_pct = warning_pct
        self.session_start_utc = session_start_utc
        self.session_end_utc = session_end_utc
        self.started_utc = started_utc or datetime.now(tz=UTC)
        self.finished_utc: datetime | None = None
        self.expected_cycles = expected_cycles
        self.tickers: dict[str, TickerHealth] = {
            ticker: TickerHealth(ticker=ticker, expected_cycles=expected_cycles)
            for ticker in tickers
        }

    # ------------------------------------------------------------ recording

    def record_cycle(self, result: CycleResult) -> None:
        for ticker_result in result.results:
            health = self.tickers.setdefault(
                ticker_result.ticker,
                TickerHealth(ticker=ticker_result.ticker, expected_cycles=self.expected_cycles),
            )
            health.contracts_discovered += ticker_result.new_contracts_discovered
            health.tracked_contract_count = max(
                health.tracked_contract_count, ticker_result.tracked_contract_count
            )
            health.option_rows_saved += ticker_result.option_rows_saved
            health.batch_failures += ticker_result.batch_failures
            if ticker_result.skipped_reason:
                health.skipped_cycles += 1
            if ticker_result.error:
                health.errors += 1
                health.last_error = ticker_result.error
                continue
            if ticker_result.success:
                health.observed_cycles += 1
                health.observed_timestamps.append(result.poll_timestamp_utc)
                health.last_successful_cycle_utc = result.poll_timestamp_utc.isoformat()

    @property
    def overall_error_count(self) -> int:
        return sum(h.errors for h in self.tickers.values())

    def status(self) -> str:
        if not self.tickers:
            return STATUS_FAILED
        worst = min(h.coverage_pct for h in self.tickers.values())
        return classify_status(worst, self.healthy_pct, self.warning_pct)

    # ------------------------------------------------------------ artefacts

    def snapshot(self) -> dict[str, Any]:
        return {
            "date": self.trading_date.isoformat(),
            "collector_started_utc": self.started_utc.isoformat(),
            "collector_finished_utc": (
                self.finished_utc.isoformat() if self.finished_utc else None
            ),
            "session_start_utc": (
                self.session_start_utc.isoformat() if self.session_start_utc else None
            ),
            "session_end_utc": (
                self.session_end_utc.isoformat() if self.session_end_utc else None
            ),
            "expected_cycles": self.expected_cycles,
            "thresholds": {
                "healthy_coverage_pct": self.healthy_pct,
                "warning_coverage_pct": self.warning_pct,
            },
            "tickers": {
                ticker: {
                    "expected_cycles": health.expected_cycles,
                    "observed_cycles": health.observed_cycles,
                    "coverage_pct": health.coverage_pct,
                    "largest_gap_minutes": health.largest_gap_minutes(
                        self.session_start_utc, self.session_end_utc if self.finished_utc else None
                    ),
                    "contracts_discovered": health.contracts_discovered,
                    "tracked_contract_count": health.tracked_contract_count,
                    "option_rows_saved": health.option_rows_saved,
                    "skipped_cycles": health.skipped_cycles,
                    "batch_failures": health.batch_failures,
                    "errors": health.errors,
                    "last_error": health.last_error,
                    "last_successful_cycle": health.last_successful_cycle_utc,
                    "observed_cycle_timestamps": [
                        stamp.isoformat() for stamp in sorted(health.observed_timestamps)
                    ],
                }
                for ticker, health in sorted(self.tickers.items())
            },
            "overall_error_count": self.overall_error_count,
            "status": self.status(),
        }

    def write(self, *, finished: bool = False) -> Path:
        """Write the daily health JSON atomically."""
        if finished and self.finished_utc is None:
            self.finished_utc = datetime.now(tz=UTC)
        path = Path(self.data_dir) / "health" / f"{self.trading_date.isoformat()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        if finished:
            logger.info("Wrote final health file %s (status=%s)", path, self.status())
        return path

    def restore(self) -> bool:
        """Seed counters from an existing same-day health file, if present.

        A restart mid-session must not make the day look like it only started
        at noon, so observed cycles, errors and the observed timestamps are
        carried forward.  A damaged file is ignored (and logged), never fatal.
        """
        path = Path(self.data_dir) / "health" / f"{self.trading_date.isoformat()}.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.error("Ignoring unreadable health file %s: %s", path, exc)
            return False
        if not isinstance(payload, dict) or payload.get("date") != self.trading_date.isoformat():
            logger.error("Ignoring health file %s: unexpected content", path)
            return False

        started = payload.get("collector_started_utc")
        if isinstance(started, str):
            with contextlib.suppress(ValueError):
                self.started_utc = datetime.fromisoformat(started)

        tickers = payload.get("tickers")
        if not isinstance(tickers, dict):
            return False
        for ticker, raw in tickers.items():
            if not isinstance(raw, dict):
                continue
            health = self.tickers.setdefault(
                ticker, TickerHealth(ticker=ticker, expected_cycles=self.expected_cycles)
            )
            health.observed_cycles = int(raw.get("observed_cycles", 0) or 0)
            health.contracts_discovered = int(raw.get("contracts_discovered", 0) or 0)
            health.tracked_contract_count = int(raw.get("tracked_contract_count", 0) or 0)
            health.option_rows_saved = int(raw.get("option_rows_saved", 0) or 0)
            health.errors = int(raw.get("errors", 0) or 0)
            health.batch_failures = int(raw.get("batch_failures", 0) or 0)
            health.skipped_cycles = int(raw.get("skipped_cycles", 0) or 0)
            health.last_error = raw.get("last_error")
            health.last_successful_cycle_utc = raw.get("last_successful_cycle")
            stamps = raw.get("observed_cycle_timestamps")
            if isinstance(stamps, list):
                restored: list[datetime] = []
                for item in stamps:
                    try:
                        restored.append(datetime.fromisoformat(str(item)))
                    except ValueError:
                        continue
                health.observed_timestamps = restored
        logger.info("Restored health counters for %s from %s", self.trading_date, path)
        return True
