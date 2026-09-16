"""Absolute wall-clock scheduler for the collector.

Timing policy
-------------
Polls happen on absolute boundaries (09:30:00, 09:31:00, ...), never on a
``collect(); sleep(60)`` cadence, which would drift by however long each cycle
takes.  After each cycle the next boundary is recomputed from the current
time, so:

* processing time never accumulates into the schedule, and
* an overrunning cycle simply misses a boundary; the scheduler resumes at the
  next future boundary instead of firing a burst of catch-up requests.

SIGINT and SIGTERM set a stop flag.  The current cycle finishes, the health
file is written, and the process exits cleanly, which is what makes systemd
restarts and VM reboots safe.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import FrameType

from config import Settings
from tradier_collector.collector import TickerCollector
from tradier_collector.cycle import CycleResult, run_cycle
from tradier_collector.expiration import ExpirationResolver
from tradier_collector.health import HealthTracker
from tradier_collector.market_clock import MarketClock, build_market_clock
from tradier_collector.storage import ensure_layout
from tradier_collector.tradier_client import TradierClient

logger = logging.getLogger(__name__)

#: Longest single sleep, so a stop signal is noticed promptly.
SLEEP_SLICE_SECONDS = 1.0
#: Write the health file every N cycles so a watchdog sees live progress.
HEALTH_WRITE_EVERY = 5


class CollectorScheduler:
    """Drives collection cycles on absolute wall-clock boundaries."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: TradierClient | None = None,
        collector: TickerCollector | None = None,
        clock: MarketClock | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.clock = clock or build_market_clock(settings)
        self._now = now
        self._sleep = sleep
        self._stop = False
        self._owns_client = client is None and collector is None

        self.client = client or TradierClient(
            settings.api_token,
            base_url=settings.base_url,
            timeout=settings.http_timeout,
            max_attempts=settings.http_max_attempts,
        )
        self.collector = collector or TickerCollector(
            settings, self.client, ExpirationResolver(self.client)
        )
        self.health: HealthTracker | None = None
        self._health_date: date | None = None
        self._cycles_since_health_write = 0

    # ------------------------------------------------------------- signals

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                logger.warning("Could not install handler for %s", sig)

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        logger.info("Received signal %s; finishing the current cycle and shutting down", signum)
        self.request_stop()

    def request_stop(self) -> None:
        self._stop = True

    @property
    def stopping(self) -> bool:
        return self._stop

    # --------------------------------------------------------------- sleep

    def _sleep_until(self, target: datetime) -> None:
        """Sleep in slices so a stop signal is acted on within a second."""
        while not self._stop:
            remaining = (target - self._now()).total_seconds()
            if remaining <= 0:
                return
            self._sleep(min(SLEEP_SLICE_SECONDS, remaining))

    # --------------------------------------------------------------- health

    def _ensure_health(self, trading_date: date) -> HealthTracker:
        if self.health is not None and self._health_date == trading_date:
            return self.health
        if self.health is not None:
            self.health.write(finished=True)
        expected = len(self.clock.expected_poll_timestamps(trading_date))
        window = self.clock.session_window(trading_date)
        tracker = HealthTracker(
            trading_date=trading_date,
            tickers=self.settings.tickers,
            expected_cycles=expected,
            data_dir=self.settings.data_dir,
            healthy_pct=self.settings.healthy_coverage_pct,
            warning_pct=self.settings.warning_coverage_pct,
            session_start_utc=window.start_utc if window else None,
            session_end_utc=window.end_utc if window else None,
        )
        # A restart mid-session must not discard the morning counters.
        tracker.restore()
        tracker.write()
        self.health = tracker
        self._health_date = trading_date
        self._cycles_since_health_write = 0
        return tracker

    def _finalise_health(self) -> None:
        if self.health is not None:
            self.health.write(finished=True)
            self.health = None
            self._health_date = None

    # ----------------------------------------------------------------- run

    def run(self, *, max_cycles: int | None = None, install_signals: bool = True) -> int:
        """Run until stopped (or until ``max_cycles`` cycles have executed)."""
        if install_signals:
            self.install_signal_handlers()
        ensure_layout(self.settings.data_dir, list(self.settings.tickers))
        logger.info("Collector starting with configuration: %s", self.settings.redacted_summary())

        cycles_run = 0
        try:
            while not self._stop and (max_cycles is None or cycles_run < max_cycles):
                now = self._now()
                trading_date = self.clock.today(now)
                window = self.clock.session_window(trading_date)

                if window is None or now > window.end_utc:
                    self._finalise_health()
                    if not self._wait_for_next_session(now):
                        break
                    continue

                if now < window.start_utc:
                    logger.info(
                        "Waiting for the %s session to open at %s",
                        trading_date.isoformat(),
                        window.start_utc.isoformat(),
                    )
                    self._sleep_until(window.start_utc)
                    continue

                boundary = self.clock.next_boundary(now)
                if boundary > window.end_utc:
                    # Nothing left to sample today.
                    self._sleep_until(window.end_utc + timedelta(seconds=1))
                    continue

                self._sleep_until(boundary)
                if self.stopping:
                    break

                self._run_one_cycle(boundary, trading_date, cycles_run)
                cycles_run += 1
        finally:
            self._finalise_health()
            if self._owns_client:
                self.client.close()
            logger.info("Collector stopped after %d cycles", cycles_run)
        return cycles_run

    def _run_one_cycle(
        self, boundary: datetime, trading_date: date, cycle_index: int
    ) -> CycleResult:
        health = self._ensure_health(trading_date)
        result = run_cycle(
            self.collector,
            tickers=self.settings.tickers,
            poll_timestamp_utc=boundary,
            trading_date=trading_date,
            cycle_index=cycle_index,
        )
        health.record_cycle(result)

        self._cycles_since_health_write += 1
        if self._cycles_since_health_write >= HEALTH_WRITE_EVERY:
            health.write()
            self._cycles_since_health_write = 0

        overrun = (self._now() - boundary).total_seconds()
        if overrun > self.settings.poll_interval_seconds:
            logger.warning(
                "Cycle %s overran its interval by %.1fs; the next boundary will be skipped "
                "rather than caught up",
                result.cycle_id,
                overrun - self.settings.poll_interval_seconds,
            )
        return result

    def _wait_for_next_session(self, now: datetime) -> bool:
        """Sleep until the next session start.  False means give up (stopped)."""
        next_start = self.clock.next_session_start(now + timedelta(minutes=1))
        if next_start is None:
            logger.error("No trading session found in the next two weeks; stopping")
            return False
        logger.info(
            "Outside the collection window; next session starts at %s (%.1f hours away)",
            next_start.isoformat(),
            (next_start - now).total_seconds() / 3600.0,
        )
        self._sleep_until(next_start)
        return not self._stop

    def run_immediate_cycle(self, moment: datetime | None = None) -> CycleResult:
        """Run exactly one cycle right now, ignoring the session window.

        Intended for manual verification (``python main.py --once``); the
        normal path is :meth:`run`.
        """
        now = (moment or self._now()).replace(microsecond=0)
        trading_date = self.clock.today(now)
        ensure_layout(self.settings.data_dir, list(self.settings.tickers))
        try:
            return self._run_one_cycle(now, trading_date, 0)
        finally:
            self._finalise_health()
            if self._owns_client:
                self.client.close()
