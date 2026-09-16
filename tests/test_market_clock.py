from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from tradier_collector.market_clock import MarketClock

NY = ZoneInfo("America/New_York")


@pytest.fixture
def clock() -> MarketClock:
    return MarketClock(
        tz=NY,
        session_open=time(9, 30),
        session_close=time(16, 15),
        poll_interval_seconds=60,
        early_close_extra_minutes=15,
    )


def test_regular_weekday_is_a_session(clock: MarketClock) -> None:
    assert clock.is_trading_day(date(2026, 9, 15))


def test_weekend_is_not_a_session(clock: MarketClock) -> None:
    assert not clock.is_trading_day(date(2026, 9, 12))  # Saturday
    assert not clock.is_trading_day(date(2026, 9, 13))  # Sunday
    assert clock.session_window(date(2026, 9, 12)) is None


def test_holiday_is_not_a_session(clock: MarketClock) -> None:
    assert not clock.is_trading_day(date(2026, 12, 25))  # Christmas
    assert not clock.is_trading_day(date(2026, 7, 3))  # Independence Day observed


def test_summer_session_bounds_are_in_edt(clock: MarketClock) -> None:
    window = clock.session_window(date(2026, 9, 15))
    assert window is not None
    assert window.start_utc == datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 9, 15, 20, 15, tzinfo=UTC)
    assert not window.is_early_close


def test_winter_session_bounds_are_in_est(clock: MarketClock) -> None:
    window = clock.session_window(date(2026, 12, 15))
    assert window is not None
    assert window.start_utc == datetime(2026, 12, 15, 14, 30, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 12, 15, 21, 15, tzinfo=UTC)


def test_dst_transition_days_keep_local_clock_times(clock: MarketClock) -> None:
    # The Monday after the November 2026 fall-back.
    window = clock.session_window(date(2026, 11, 2))
    assert window is not None
    assert window.start_utc.astimezone(NY).strftime("%H:%M") == "09:30"
    assert window.end_utc.astimezone(NY).strftime("%H:%M") == "16:15"
    # The Monday after the March 2026 spring-forward.
    spring = clock.session_window(date(2026, 3, 9))
    assert spring is not None
    assert spring.start_utc.astimezone(NY).strftime("%H:%M") == "09:30"


def test_early_close_uses_the_calendar_close_plus_the_configured_extension(clock: MarketClock) -> None:
    day = date(2026, 11, 27)  # day after Thanksgiving
    assert clock.is_early_close(day)
    window = clock.session_window(day)
    assert window is not None
    assert window.equity_close_utc.astimezone(NY).strftime("%H:%M") == "13:00"
    assert window.end_utc.astimezone(NY).strftime("%H:%M") == "13:15"


def test_early_close_extension_is_configurable() -> None:
    strict = MarketClock(
        tz=NY,
        session_open=time(9, 30),
        session_close=time(16, 15),
        poll_interval_seconds=60,
        early_close_extra_minutes=0,
    )
    window = strict.session_window(date(2026, 11, 27))
    assert window is not None
    assert window.end_utc.astimezone(NY).strftime("%H:%M") == "13:00"


def test_expected_poll_timestamps_cover_the_session(clock: MarketClock) -> None:
    stamps = clock.expected_poll_timestamps(date(2026, 9, 15))
    assert len(stamps) == 406  # 09:30 through 16:15 inclusive
    assert stamps[0] == datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    assert stamps[-1] == datetime(2026, 9, 15, 20, 15, tzinfo=UTC)
    assert all(ts.second == 0 for ts in stamps)


def test_expected_poll_timestamps_shrink_on_an_early_close(clock: MarketClock) -> None:
    assert len(clock.expected_poll_timestamps(date(2026, 11, 27))) == 226
    assert clock.expected_poll_timestamps(date(2026, 9, 12)) == []


def test_next_boundary_is_the_next_whole_minute(clock: MarketClock) -> None:
    moment = datetime(2026, 9, 15, 13, 30, 17, 250_000, tzinfo=UTC)
    assert clock.next_boundary(moment) == datetime(2026, 9, 15, 13, 31, tzinfo=UTC)
    exact = datetime(2026, 9, 15, 13, 31, tzinfo=UTC)
    assert clock.next_boundary(exact) == datetime(2026, 9, 15, 13, 32, tzinfo=UTC)


def test_next_boundary_honours_a_longer_interval() -> None:
    five = MarketClock(
        tz=NY,
        session_open=time(9, 30),
        session_close=time(16, 15),
        poll_interval_seconds=300,
    )
    moment = datetime(2026, 9, 15, 13, 32, 10, tzinfo=UTC)
    assert five.next_boundary(moment) == datetime(2026, 9, 15, 13, 35, tzinfo=UTC)


def test_session_open_check(clock: MarketClock) -> None:
    assert clock.is_session_open(datetime(2026, 9, 15, 14, 0, tzinfo=UTC))
    assert not clock.is_session_open(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    assert not clock.is_session_open(datetime(2026, 9, 12, 14, 0, tzinfo=UTC))


def test_next_session_start_skips_weekends_and_holidays(clock: MarketClock) -> None:
    friday_evening = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    nxt = clock.next_session_start(friday_evening)
    assert nxt is not None
    assert nxt.astimezone(NY).date() == date(2026, 9, 14)

    during = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    assert clock.next_session_start(during) == during


def test_trading_days_in_range(clock: MarketClock) -> None:
    days = clock.trading_days(date(2026, 9, 11), date(2026, 9, 15))
    assert days == [date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15)]
    assert clock.trading_days(date(2026, 9, 15), date(2026, 9, 11)) == []
