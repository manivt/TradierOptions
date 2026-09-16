"""Trading-calendar and scheduling arithmetic.

Everything that needs to know "is the market open" or "when is the next poll
boundary" lives here, so the timezone rules exist in exactly one place.

Timezone policy
---------------
* America/New_York is used only for trading dates, session boundaries and
  resolving the same-day expiration.
* Every value that leaves this module is an aware UTC datetime.
* Session bounds are built by attaching the configured local clock times to a
  local calendar date and then converting to UTC.  This is DST-correct by
  construction: 09:30 New York is 13:30 UTC in summer and 14:30 UTC in winter,
  and we never do naive arithmetic across a DST boundary.

Early closes
------------
On NYSE half days (typically the day after Thanksgiving, Christmas Eve and
July 3) the equity market closes at 13:00 ET instead of 16:00 ET.  On regular
days, options on SPY/QQQ/IWM keep trading for 15 minutes after the equity
close (16:15 ET).  Published exchange holiday schedules are not unambiguous
about whether that same 15-minute extension applies on half days, and
``exchange_calendars`` models the *equity* session only, so it cannot answer
the question for us.

The rule implemented here is therefore explicit and configurable:

    early-close option session end = equity close (from XNYS)
                                     + EARLY_CLOSE_OPTION_EXTRA_MINUTES

The default is 15 minutes, mirroring the regular session.  Set the variable to
0 to stop exactly at the equity close.  Polling a few minutes past the real
option close is harmless (quotes simply stop changing); stopping early would
lose real data, so the default errs towards collecting more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Protocol, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

logger = logging.getLogger(__name__)

CALENDAR_NAME = "XNYS"
#: Regular NYSE equity close.  A session closing earlier than this is a half day.
REGULAR_EQUITY_CLOSE = time(16, 0)


@lru_cache(maxsize=4)
def get_calendar(name: str = CALENDAR_NAME) -> xcals.ExchangeCalendar:
    """Cached calendar instance (construction is comparatively expensive)."""
    return xcals.get_calendar(name)


@dataclass(frozen=True)
class SessionWindow:
    """The collection window for one trading date."""

    trading_date: date
    start_utc: datetime
    end_utc: datetime
    is_early_close: bool
    equity_close_utc: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start_utc <= moment <= self.end_utc


class MarketClock:
    """Trading-day questions and poll-boundary arithmetic."""

    def __init__(
        self,
        *,
        tz: ZoneInfo,
        session_open: time,
        session_close: time,
        poll_interval_seconds: int,
        early_close_extra_minutes: int = 15,
        calendar_name: str = CALENDAR_NAME,
    ) -> None:
        self.tz = tz
        self.session_open = session_open
        self.session_close = session_close
        self.poll_interval_seconds = poll_interval_seconds
        self.early_close_extra_minutes = early_close_extra_minutes
        self.calendar = get_calendar(calendar_name)

    # ------------------------------------------------------------ calendars

    def today(self, now_utc: datetime | None = None) -> date:
        """The current local (New York) calendar date."""
        moment = now_utc or datetime.now(tz=UTC)
        return moment.astimezone(self.tz).date()

    def is_trading_day(self, day: date) -> bool:
        return bool(self.calendar.is_session(day.isoformat()))

    def trading_days(self, start: date, end: date) -> list[date]:
        """All XNYS sessions in the inclusive range."""
        if end < start:
            return []
        sessions = self.calendar.sessions_in_range(start.isoformat(), end.isoformat())
        return [ts.date() for ts in sessions]

    def equity_close(self, day: date) -> datetime:
        """The scheduled equity close for a session, as aware UTC."""
        close = self.calendar.session_close(day.isoformat())
        moment = close.to_pydatetime()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return cast(datetime, moment.astimezone(UTC))

    def is_early_close(self, day: date) -> bool:
        if not self.is_trading_day(day):
            return False
        local_close = self.equity_close(day).astimezone(self.tz).time()
        return local_close < REGULAR_EQUITY_CLOSE

    # ------------------------------------------------------------- sessions

    def _local_to_utc(self, day: date, clock: time) -> datetime:
        return datetime.combine(day, clock, tzinfo=self.tz).astimezone(UTC)

    def session_window(self, day: date) -> SessionWindow | None:
        """The collection window for ``day``, or None when it is not a session."""
        if not self.is_trading_day(day):
            return None

        equity_close_utc = self.equity_close(day)
        early = self.is_early_close(day)
        start_utc = self._local_to_utc(day, self.session_open)

        if early:
            end_utc = equity_close_utc + timedelta(minutes=self.early_close_extra_minutes)
            logger.info(
                "%s is an NYSE early close (equity close %s ET); option collection ends %s ET",
                day.isoformat(),
                equity_close_utc.astimezone(self.tz).strftime("%H:%M"),
                end_utc.astimezone(self.tz).strftime("%H:%M"),
            )
        else:
            end_utc = self._local_to_utc(day, self.session_close)

        if end_utc <= start_utc:
            logger.warning("Degenerate session window for %s; skipping day", day.isoformat())
            return None

        return SessionWindow(
            trading_date=day,
            start_utc=start_utc,
            end_utc=end_utc,
            is_early_close=early,
            equity_close_utc=equity_close_utc,
        )

    def is_session_open(self, moment: datetime) -> bool:
        window = self.session_window(self.today(moment))
        return window is not None and window.contains(moment)

    # ------------------------------------------------------------ boundaries

    def next_boundary(self, moment: datetime) -> datetime:
        """The next absolute wall-clock poll boundary strictly after ``moment``.

        Boundaries are multiples of the poll interval measured from the Unix
        epoch, so a 60-second interval lands on :00 of every minute and never
        drifts with processing time.  New York offsets are whole hours, so
        epoch-aligned boundaries are also aligned in local time.
        """
        interval = self.poll_interval_seconds
        epoch_seconds = moment.timestamp()
        next_tick = (int(epoch_seconds) // interval + 1) * interval
        return datetime.fromtimestamp(next_tick, tz=UTC)

    def expected_poll_timestamps(self, day: date) -> list[datetime]:
        """Every poll timestamp we intend to sample on ``day``.

        QA compares observed cycles against this explicit list rather than an
        approximate count, so holidays, half days and DST cannot quietly
        inflate or deflate the expectation.
        """
        window = self.session_window(day)
        if window is None:
            return []
        interval = timedelta(seconds=self.poll_interval_seconds)
        stamps: list[datetime] = []
        # The first boundary is the session open itself when it is aligned,
        # otherwise the first aligned boundary after it.
        current = window.start_utc
        if int(current.timestamp()) % self.poll_interval_seconds != 0:
            current = self.next_boundary(current)
        while current <= window.end_utc:
            stamps.append(current)
            current += interval
        return stamps

    def next_session_start(self, moment: datetime) -> datetime | None:
        """Start of the next collection window at or after ``moment``."""
        day = self.today(moment)
        for offset in range(0, 15):
            candidate = day + timedelta(days=offset)
            window = self.session_window(candidate)
            if window is None:
                continue
            if moment <= window.start_utc:
                return window.start_utc
            if window.contains(moment):
                return moment
        return None


class ClockSettings(Protocol):
    """The subset of Settings this module needs (keeps the import one-way)."""

    # Read-only members: Settings is a frozen dataclass.
    @property
    def tz(self) -> ZoneInfo: ...

    @property
    def option_session_open(self) -> time: ...

    @property
    def option_session_close(self) -> time: ...

    @property
    def poll_interval_seconds(self) -> int: ...

    @property
    def early_close_option_extra_minutes(self) -> int: ...


def build_market_clock(settings: ClockSettings) -> MarketClock:
    """Construct a :class:`MarketClock` from a Settings-like object."""
    return MarketClock(
        tz=settings.tz,
        session_open=settings.option_session_open,
        session_close=settings.option_session_close,
        poll_interval_seconds=settings.poll_interval_seconds,
        early_close_extra_minutes=settings.early_close_option_extra_minutes,
    )
