# 09 - Collection cycle

[Index](README.md) | Prev: [08 - Sticky universe](08-sticky-universe.md) | Next: [10 - Schemas and normalization](10-schemas-and-normalization.md)

Code: `tradier_collector/collector.py`, `tradier_collector/cycle.py`
Tests: `tests/test_collector.py` (14), `tests/test_cycle.py` (8)

## Per-ticker flow (`TickerCollector._collect_inner`)

1. `get_quote(ticker)` with request start/end timestamps recorded.
2. `extract_spot(quote)` - last, else mid, else previous close, else `None`.
3. Persist the underlying row (happens **before** anything option-related, so an
   options problem never costs us the underlying series).
4. `zero_dte(ticker, day)`; `None` sets `skipped_reason="no_0dte_expiration"` and returns.
5. Load/reuse the sticky tracker for this ticker-day.
6. If spot exists and this is a discovery cycle: fetch chain, select window,
   merge new contracts.
7. `tracker.symbols()` - every tracked OCC symbol.
8. Quote them in `QUOTE_BATCH_SIZE` batches, each batch timed independently.
9. Normalize into canonical rows, dropping only rows with no usable symbol.
10. Persist option rows.

## Failure isolation

`collect()` wraps `_collect_inner` and catches `TradierAPIError` and any other
`Exception`, recording it in `TickerResult.error` with a traceback in the log.
It never raises (I9).  `TickerResult`:

```text
ticker  success  underlying_saved  new_contracts_discovered  tracked_contract_count
option_rows_saved  batch_failures  missing_symbols  elapsed_seconds
skipped_reason  error  spot  expiration
```

Semantics worth remembering:

* `success=True` with a `skipped_reason` is a *normal* outcome (no 0DTE, empty
  universe) - it is not counted as a failure anywhere.
* A partially failed set of batches still persists the rows that succeeded and
  reports `batch_failures` (I16).  Only "every batch failed and nothing was
  collected" sets `error`.
* `missing_symbols` counts tracked contracts absent from the response; the first
  ten are logged.  Missing quotes are a data fact, not a ticker failure.

## Cycle aggregation (`cycle.run_cycle`)

Creates one `CycleContext` (cycle_id + poll timestamp + trading date + index)
and iterates the tickers sequentially, appending each `TickerResult`.  Sequential
is deliberate: three tickers take well under a second of wall clock, and
concurrency would add failure modes for no benefit at this scale.

`make_cycle_id(poll_timestamp)` -> `20260915T133000Z-1a2b3c4d`.  The timestamp
prefix makes files self-describing months later and sorts naturally; the random
suffix prevents two processes (for example, an overlapping restart) from
colliding on the same boundary.

`log_cycle_summary` emits one greppable line per cycle:

```text
cycle <id> at <ts>: ok=SPY,QQQ failed=- skipped=- new_contracts=0 tracked=126
option_rows=126 batch_failures=0 elapsed=0.84s
```

plus a separate ERROR line per failed ticker.

## Where timestamps come from

| Field | Source |
| --- | --- |
| `poll_timestamp_utc` | the scheduler boundary (never Tradier) |
| `request_started_utc` / `request_completed_utc` | measured around each HTTP call; for options these are the **batch** timings |
| `trade_date` / `bid_date` / `ask_date` | Tradier epoch milliseconds |
| `tradier_greeks_updated_at` | Tradier `greeks.updated_at` |

Keeping request timings lets later research measure how stale a quote was
relative to the intended sample point.

Related: [10 - Schemas and normalization](10-schemas-and-normalization.md), [11 - Storage](11-storage.md)
