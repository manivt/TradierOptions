# 07 - Expiration and discovery

[Index](README.md) | Prev: [06 - Scheduler](06-scheduler.md) | Next: [08 - Sticky universe](08-sticky-universe.md)

Code: `expiration.py`, `strike_selector.py`, `discovery.py`
Tests: `tests/test_expiration.py` (7), `tests/test_strike_selector.py` (11)

## Step 1 - is there a 0DTE today?

`ExpirationResolver.zero_dte(ticker, trading_date) -> str | None`

* The expiration list is cached per `(ticker, trading_date)`; entries for other
  dates are dropped whenever a new date is queried, so a long-running process
  cannot accumulate stale days or unbounded memory.
* A miss triggers exactly **one** forced refresh, then returns `None`.  Reason: a
  list cached moments before a new listing appeared is the only benign
  explanation for a miss, and the extra request happens at most once per ticker
  per day.
* `None` is logged at INFO and skips options for that ticker this cycle.  The
  underlying quote is still saved (I14).
* API failures propagate as `TradierAPIError` - a broken API is not the same as
  "no 0DTE", and conflating them would hide an outage.

## Step 2 - select the ATM window

`select_atm_window(chain, spot, strikes_each_side) -> StrikeWindow`

1. Drop malformed entries (no symbol, no/negative strike, option_type not
   call/put) and log how many were ignored.
2. Unique strikes, sorted ascending.
3. `find_atm_strike` - closest to spot; **exact ties resolve to the lower
   strike** (strict `<` comparison with a 1e-9 epsilon keeps the earlier entry).
4. Slice ATM-N .. ATM+N, clamped to the available range; `truncated_low` /
   `truncated_high` flags record clamping and a warning is logged.
5. Keep both calls and puts for the selected strikes.  A strike missing one side
   is logged but never fatal.

Typical result: 21 strikes, 42 contracts.  Works for whole-dollar and half-dollar
strike ladders (tested).

## Step 3 - merge into the sticky universe

`discovery.discover_window` fetches the chain and selects the window;
`discovery.apply_discovery` hands the contracts to the `ContractTracker` and
returns only the genuinely new ones (for logging and health counters).

## Discovery frequency

`DISCOVERY_INTERVAL_CYCLES` (default 1) controls how often the full chain is
fetched.  `TickerCollector._should_discover` always discovers when the universe
is empty, otherwise every N cycles.  Because the universe is sticky, a skipped
discovery can only *delay* picking up a brand-new strike - it can never lose an
existing one.  That is what makes this knob safe to raise if rate limits or
small-VM CPU ever become a concern.

If spot is unavailable this cycle, discovery is skipped entirely (there is no
sensible ATM without a price) but the existing universe is still quoted.

Related: [08 - Sticky universe](08-sticky-universe.md), [09 - Collection cycle](09-collection-cycle.md)
