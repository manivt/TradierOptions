# 05 - Market clock

[Index](README.md) | Prev: [04 - Tradier API client](04-tradier-client.md) | Next: [06 - Scheduler](06-scheduler.md)

Code: `tradier_collector/market_clock.py` | Tests: `tests/test_market_clock.py` (15 tests)

Every "is the market open" and "when is the next poll" question lives here, so
the timezone rules exist in exactly one place.

## API

```python
MarketClock(tz, session_open, session_close, poll_interval_seconds,
            early_close_extra_minutes=15, calendar_name="XNYS")
  .today(now_utc)              -> date        (New York calendar date)
  .is_trading_day(day)         -> bool
  .trading_days(start, end)    -> list[date]
  .equity_close(day)           -> datetime    (UTC, from XNYS)
  .is_early_close(day)         -> bool
  .session_window(day)         -> SessionWindow | None
  .is_session_open(moment)     -> bool
  .next_boundary(moment)       -> datetime    (strictly future, epoch-aligned)
  .expected_poll_timestamps(day) -> list[datetime]
  .next_session_start(moment)  -> datetime | None
build_market_clock(settings)   -> MarketClock   (accepts any ClockSettings)
```

`ClockSettings` is a `Protocol` with read-only properties, so this module does
not import `config` - that keeps QA tools importable without the collector.
The properties are read-only because `Settings` is a frozen dataclass.

## Timezone policy

* America/New_York is used **only** for trading dates, session bounds and
  resolving today expiration.
* Everything returned is aware UTC.
* Bounds are built as `datetime.combine(day, clock_time, tzinfo=NY).astimezone(UTC)`.
  This is DST-correct by construction: 09:30 New York is 13:30 UTC in summer and
  14:30 UTC in winter, and no naive arithmetic ever crosses a DST boundary.
  Verified for both 2026 transitions in the tests.

## Early closes (the documented open question)

`exchange_calendars` models the **equity** session only. On NYSE half days it
reports a 13:00 ET close. Cboe's [2026 U.S. Options holiday
schedule](https://www.cboe.com/about/hours/us-options) lists 13:00 ET for BZX,
C2 and EDGX Options, while Cboe C1's [24/5 FAQ](https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-options-exchange-245-faq)
lists 13:15 ET RTH on the Thanksgiving early-close Friday. SPY, QQQ and IWM are
multiply listed and Tradier supplies consolidated quotes, so the collector
deliberately uses the later 13:15 bound by default. The calendar library cannot
express that venue policy.

The implemented rule is explicit and configurable:

```text
early-close option session end = XNYS equity close + EARLY_CLOSE_OPTION_EXTRA_MINUTES
```

Default 15 minutes. Set it to `0` to restrict collection to the 13:00 ET venues.
The default retains quotes from the C1 session through 13:15; review this policy
if Tradier changes the venues included in its consolidated feed.

## Boundary arithmetic

`next_boundary` returns the next multiple of `poll_interval_seconds` measured
from the Unix epoch, strictly after the given moment.  For 60 seconds that is
:00 of every minute.  New York offsets are whole hours, so epoch-aligned
boundaries are also aligned in local time for any interval that divides an hour.

`expected_poll_timestamps(day)` materialises **every** intended sample for a
session (406 for a regular day, 226 for a 13:15 half day).  QA and health
compare against this list rather than a computed count, so holidays, half days
and DST cannot quietly inflate or deflate the expectation (I17).

Related: [06 - Scheduler](06-scheduler.md), [12 - Health, QA, watchdog](12-health-qa-watchdog.md)
