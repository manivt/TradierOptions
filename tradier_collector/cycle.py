"""One scheduler cycle across all configured tickers.

The cycle owns the shared identity of a sample: a single ``cycle_id`` and a
single ``poll_timestamp_utc`` are used for every ticker and every option row
in the cycle, so rows can be aligned across SPY, QQQ and IWM by cycle alone.

The poll timestamp is the boundary the scheduler intended to sample, never a
timestamp taken from a Tradier payload.  Vendor timestamps are preserved
separately (trade_date, bid_date, ask_date, tradier_greeks_updated_at).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .collector import CycleContext, TickerCollector, TickerResult

logger = logging.getLogger(__name__)


def make_cycle_id(poll_timestamp_utc: datetime) -> str:
    """A stable, sortable, collision-resistant cycle identifier.

    The timestamp prefix makes files self-describing when read months later;
    the random suffix keeps two processes (for example a restarted collector)
    from colliding on the same boundary.
    """
    stamp = poll_timestamp_utc.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class CycleResult:
    cycle_id: str
    poll_timestamp_utc: datetime
    trading_date: date
    results: list[TickerResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def successful_tickers(self) -> list[str]:
        return [r.ticker for r in self.results if r.success]

    @property
    def failed_tickers(self) -> list[str]:
        return [r.ticker for r in self.results if not r.success]

    @property
    def skipped_tickers(self) -> list[str]:
        return [r.ticker for r in self.results if r.skipped_reason]

    @property
    def new_contracts_discovered(self) -> int:
        return sum(r.new_contracts_discovered for r in self.results)

    @property
    def tracked_contract_count(self) -> int:
        return sum(r.tracked_contract_count for r in self.results)

    @property
    def option_rows_saved(self) -> int:
        return sum(r.option_rows_saved for r in self.results)

    @property
    def batch_failures(self) -> int:
        return sum(r.batch_failures for r in self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "poll_timestamp_utc": self.poll_timestamp_utc.isoformat(),
            "trading_date": self.trading_date.isoformat(),
            "elapsed_seconds": self.elapsed_seconds,
            "successful_tickers": self.successful_tickers,
            "failed_tickers": self.failed_tickers,
            "skipped_tickers": self.skipped_tickers,
            "new_contracts_discovered": self.new_contracts_discovered,
            "tracked_contract_count": self.tracked_contract_count,
            "option_rows_saved": self.option_rows_saved,
            "batch_failures": self.batch_failures,
            "tickers": [r.as_dict() for r in self.results],
        }


def run_cycle(
    collector: TickerCollector,
    *,
    tickers: tuple[str, ...],
    poll_timestamp_utc: datetime,
    trading_date: date,
    cycle_index: int = 0,
) -> CycleResult:
    """Run one full cycle; one ticker failing never affects the others."""
    cycle_id = make_cycle_id(poll_timestamp_utc)
    ctx = CycleContext(
        cycle_id=cycle_id,
        poll_timestamp_utc=poll_timestamp_utc,
        trading_date=trading_date,
        cycle_index=cycle_index,
        tickers=tickers,
    )
    started = time.monotonic()
    result = CycleResult(
        cycle_id=cycle_id,
        poll_timestamp_utc=poll_timestamp_utc,
        trading_date=trading_date,
    )
    for ticker in tickers:
        result.results.append(collector.collect(ticker, ctx))
    result.elapsed_seconds = round(time.monotonic() - started, 3)
    log_cycle_summary(result)
    return result


def log_cycle_summary(result: CycleResult) -> None:
    """One concise, greppable line per cycle."""
    logger.info(
        "cycle %s at %s: ok=%s failed=%s skipped=%s new_contracts=%d tracked=%d "
        "option_rows=%d batch_failures=%d elapsed=%.2fs",
        result.cycle_id,
        result.poll_timestamp_utc.isoformat(),
        ",".join(result.successful_tickers) or "-",
        ",".join(result.failed_tickers) or "-",
        ",".join(result.skipped_tickers) or "-",
        result.new_contracts_discovered,
        result.tracked_contract_count,
        result.option_rows_saved,
        result.batch_failures,
        result.elapsed_seconds,
    )
    for ticker_result in result.results:
        if ticker_result.error:
            logger.error(
                "cycle %s: %s failed: %s",
                result.cycle_id,
                ticker_result.ticker,
                ticker_result.error,
            )
