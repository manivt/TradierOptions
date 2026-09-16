# 01 - Domain primer

[Index](README.md) | Prev: [00 - Architecture](00-architecture.md) | Next: [02 - Invariants](02-invariants.md)

Terms an agent needs before reading the code.

## 0DTE

"Zero days to expiration": an option whose expiration date is today.  SPY, QQQ
and IWM list expirations every weekday, so most sessions have one - but **not
guaranteed**, which is why `ExpirationResolver.zero_dte` returns `None` rather
than assuming.  A missing 0DTE is a skip, never an error.

0DTE contracts have extreme gamma/theta behaviour and their quotes go wide and
often one-sided late in the day.  A null bid or ask is normal market structure,
**not** a collector failure - QA treats "every contract null on both sides for a
whole cycle" as suspicious, but scattered nulls as expected.

## Spot, ATM, and the discovery window

* **spot** - current underlying price.  Derived by `normalization.extract_spot`:
  last trade, else bid/ask midpoint, else previous close, else `None`.
* **ATM strike** - the listed strike closest to spot.  On an exact midpoint tie
  the **lower** strike wins (`strike_selector.find_atm_strike`), deterministically.
* **discovery window** - ATM plus `DISCOVERY_STRIKES_EACH_SIDE` (default 10)
  strikes below and above, both calls and puts.  Normally 21 strikes / 42
  contracts.  Truncation at a chain edge returns what exists and logs a warning.

## Sticky universe

The union of every contract that has *ever* entered the discovery window today.
It only grows during a session.  See [08 - Sticky universe](08-sticky-universe.md);
this is the single most important behaviour in the project.

## Cycle

One scheduler tick.  All tickers in a cycle share:

* `cycle_id` - `YYYYMMDDThhmmssZ-<8 hex>`, sortable and collision-resistant.
* `poll_timestamp_utc` - the wall-clock boundary we *intended* to sample.

This makes cross-ticker joins exact: SPY, QQQ and IWM rows for the same minute
carry the same `cycle_id`.

## OCC option symbols

Tradier returns symbols such as `SPY260915C00600000`:
`SPY` root, `260915` expiry (YYMMDD), `C` call / `P` put, `00600000` strike in
thousandths (600.000).  The collector never parses these - it uses the `strike`
and `option_type` fields Tradier supplies - but tests construct them, and they
are human-readable when eyeballing data.

## Sessions and early closes

Regular equity session 09:30-16:00 ET; ETF options trade until 16:15 ET, which
is the collector default close.  NYSE half days close at 13:00 ET.  Whether ETF
options keep the extra 15 minutes on half days is genuinely ambiguous in
published schedules, so it is a configurable rule.  See
[05 - Market clock](05-market-clock.md).

## Vendor greeks vs model greeks

Tradier greeks are **not** minute-resolution; they carry their own
`greeks.updated_at`.  They are stored under `tradier_*` names with the vendor
timestamp preserved.  Locally computed greeks (future work) must use `model_*`
names.  Mixing the two would silently corrupt any volatility research.

Related: [07 - Expiration and discovery](07-expiration-and-discovery.md),
[10 - Schemas and normalization](10-schemas-and-normalization.md),
[17 - Data dictionary](17-data-dictionary.md)
