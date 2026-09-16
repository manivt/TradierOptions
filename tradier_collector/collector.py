"""Per-ticker collection for a single cycle.

Every failure mode is contained here: a bad underlying quote, a missing 0DTE
expiration, a failed chain request or a failed quote batch degrade this one
ticker only.  The cycle layer above never sees an exception from a ticker, it
sees a :class:`TickerResult`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from config import Settings

from .contract_tracker import ContractTracker
from .discovery import apply_discovery, discover_window
from .expiration import ExpirationResolver
from .normalization import (
    extract_spot,
    normalize_option_quote,
    normalize_underlying_quote,
)
from .storage import (
    append_option_snapshot,
    append_underlying_snapshot,
    metadata_path,
)
from .tradier_client import TradierAPIError, TradierClient, batched

logger = logging.getLogger(__name__)


@dataclass
class TickerResult:
    """Structured outcome of one ticker collection."""

    ticker: str
    success: bool = False
    underlying_saved: int = 0
    new_contracts_discovered: int = 0
    tracked_contract_count: int = 0
    option_rows_saved: int = 0
    batch_failures: int = 0
    missing_symbols: int = 0
    elapsed_seconds: float = 0.0
    skipped_reason: str | None = None
    error: str | None = None
    spot: float | None = None
    expiration: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CycleContext:
    """Identity and timing shared by every ticker within one cycle."""

    cycle_id: str
    poll_timestamp_utc: datetime
    trading_date: date
    cycle_index: int = 0
    tickers: tuple[str, ...] = field(default_factory=tuple)


class TickerCollector:
    """Collects one ticker per cycle, reusing the sticky universe across cycles."""

    def __init__(
        self,
        settings: Settings,
        client: TradierClient,
        expirations: ExpirationResolver,
        *,
        data_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.expirations = expirations
        self.data_dir = Path(data_dir or settings.data_dir)
        self._trackers: dict[tuple[str, date], ContractTracker] = {}

    def tracker(self, ticker: str, trading_date: date) -> ContractTracker:
        """The sticky universe for a ticker/date, loaded from disk on first use."""
        key = (ticker, trading_date)
        tracker = self._trackers.get(key)
        if tracker is None:
            tracker = ContractTracker(
                metadata_path(self.data_dir, ticker, trading_date),
                ticker=ticker,
                trading_date=trading_date,
            )
            tracker.load()
            # Only the current day is kept in memory; older days cannot grow.
            self._trackers = {k: v for k, v in self._trackers.items() if k[1] == trading_date}
            self._trackers[key] = tracker
        return tracker

    # ------------------------------------------------------------- helpers

    def _should_discover(self, ctx: CycleContext, tracker: ContractTracker) -> bool:
        if len(tracker) == 0:
            return True
        interval = max(1, self.settings.discovery_interval_cycles)
        return ctx.cycle_index % interval == 0

    def _collect_quotes(
        self, symbols: list[str]
    ) -> tuple[list[tuple[dict[str, Any], datetime, datetime]], int, set[str]]:
        """Fetch tracked-contract quotes in batches.

        Returns the raw quotes (each with its own request timing), the number
        of failed batches and the set of symbols that were requested but not
        returned.  A failed batch never aborts the remaining batches.
        """
        collected: list[tuple[dict[str, Any], datetime, datetime]] = []
        returned: set[str] = set()
        failures = 0
        for batch in batched(symbols, self.settings.quote_batch_size):
            started = datetime.now(tz=UTC)
            try:
                quotes = self.client.get_quotes(batch, greeks=True)
            except TradierAPIError:
                failures += 1
                logger.exception(
                    "Quote batch failed (%d symbols, first=%s); continuing with remaining batches",
                    len(batch),
                    batch[0] if batch else "n/a",
                )
                continue
            completed = datetime.now(tz=UTC)
            for quote in quotes:
                symbol = quote.get("symbol")
                if isinstance(symbol, str):
                    returned.add(symbol)
                collected.append((quote, started, completed))
        missing = set(symbols) - returned
        if missing:
            logger.warning(
                "%d tracked contracts missing from the quote response: %s",
                len(missing),
                sorted(missing)[:10],
            )
        return collected, failures, missing

    # ------------------------------------------------------------ main flow

    def collect(self, ticker: str, ctx: CycleContext) -> TickerResult:
        """Collect one ticker.  Never raises: failures land in the result."""
        result = TickerResult(ticker=ticker)
        started = time.monotonic()
        try:
            self._collect_inner(ticker, ctx, result)
            result.success = result.error is None
        except TradierAPIError as exc:
            result.error = str(exc)
            logger.exception("Tradier API failure while collecting %s", ticker)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Unexpected failure while collecting %s", ticker)
        finally:
            result.elapsed_seconds = round(time.monotonic() - started, 3)
        return result

    def _collect_inner(self, ticker: str, ctx: CycleContext, result: TickerResult) -> None:
        # 1-3: underlying quote, spot, persistence.
        request_started = datetime.now(tz=UTC)
        quote = self.client.get_quote(ticker, greeks=False)
        request_completed = datetime.now(tz=UTC)

        spot = extract_spot(quote)
        result.spot = spot
        underlying_row = normalize_underlying_quote(
            quote,
            cycle_id=ctx.cycle_id,
            poll_timestamp_utc=ctx.poll_timestamp_utc,
            request_started_utc=request_started,
            request_completed_utc=request_completed,
            ticker=ticker,
        )
        result.underlying_saved = append_underlying_snapshot(
            [underlying_row],
            data_dir=self.data_dir,
            ticker=ticker,
            day=ctx.trading_date,
        )

        # 4: today expiration, if the ticker has one at all.
        expiration = self.expirations.zero_dte(ticker, ctx.trading_date)
        if expiration is None:
            result.skipped_reason = "no_0dte_expiration"
            return
        result.expiration = expiration

        tracker = self.tracker(ticker, ctx.trading_date)
        result.tracked_contract_count = len(tracker)

        # 5-6: discovery into the sticky universe.
        if spot is None:
            logger.warning(
                "%s has no usable spot price this cycle; skipping discovery but "
                "continuing to quote the existing universe",
                ticker,
            )
        elif self._should_discover(ctx, tracker):
            window = discover_window(
                self.client,
                ticker=ticker,
                expiration=expiration,
                spot=spot,
                strikes_each_side=self.settings.discovery_strikes_each_side,
            )
            new_contracts = apply_discovery(
                tracker,
                window,
                expiration=expiration,
                discovered_utc=ctx.poll_timestamp_utc,
            )
            result.new_contracts_discovered = len(new_contracts)

        # 7-9: quote every tracked contract, sticky or freshly discovered.
        symbols = tracker.symbols()
        result.tracked_contract_count = len(symbols)
        if not symbols:
            result.skipped_reason = result.skipped_reason or "empty_universe"
            logger.warning("%s has an empty contract universe this cycle", ticker)
            return

        quotes, failures, missing = self._collect_quotes(symbols)
        result.batch_failures = failures
        result.missing_symbols = len(missing)

        rows: list[dict[str, Any]] = []
        for raw, batch_started, batch_completed in quotes:
            row = normalize_option_quote(
                raw,
                cycle_id=ctx.cycle_id,
                poll_timestamp_utc=ctx.poll_timestamp_utc,
                request_started_utc=batch_started,
                request_completed_utc=batch_completed,
                ticker=ticker,
                expiration=expiration,
                underlying_spot=spot,
            )
            if row is not None:
                rows.append(row)

        # 10: persist.  Partial batches are still worth keeping.
        result.option_rows_saved = append_option_snapshot(
            rows,
            data_dir=self.data_dir,
            ticker=ticker,
            day=ctx.trading_date,
        )
        if failures and not rows:
            result.error = f"all {failures} quote batches failed"
