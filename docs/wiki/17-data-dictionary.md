# 17 - Data dictionary

[Index](README.md) | Prev: [16 - Decision log](16-decision-log.md) | Next: [18 - Failure modes](18-failure-modes.md)

Authoritative definition: `tradier_collector/schemas.py`. This page explains
what each field means for research use.

## Options - `data/<TICKER>/options/YYYY-MM-DD.parquet`

One row per (cycle, contract). Key: `(cycle_id, symbol)`.

| Field | Type | Meaning |
| --- | --- | --- |
| `cycle_id` | string | Shared by every ticker sampled in that minute |
| `poll_timestamp_utc` | ts UTC | The boundary we intended to sample; the alignment join key |
| `request_started_utc` / `request_completed_utc` | ts UTC | Timing of the batch request that produced this row; the gap to `poll_timestamp_utc` measures latency |
| `ticker` | string | SPY, QQQ, IWM |
| `expiration` | string | `YYYY-MM-DD`, always equal to the trading date (0DTE) |
| `underlying_spot` | float64 | Spot used for discovery this cycle |
| `symbol` | string | OCC contract symbol |
| `strike` | float64 | |
| `option_type` | string | `call` or `put` |
| `bid` / `ask` | float64 | Null is normal for far out-of-the-money 0DTE |
| `bid_size` / `ask_size` | int64 | Contracts at the top of book; liquidity filter input |
| `last` | float64 | Last trade price |
| `volume` | int64 | Session volume for the contract |
| `open_interest` | int64 | Vendor value, updated daily |
| `trade_date` / `bid_date` / `ask_date` | ts UTC | Vendor event times; staleness indicators |
| `tradier_delta/gamma/theta/vega/rho` | float64 | Vendor greeks, verbatim |
| `tradier_bid_iv/mid_iv/ask_iv/smv_vol` | float64 | Vendor IV surface values |
| `tradier_greeks_updated_at` | ts UTC | When the vendor last updated the greeks, not our sample time |

## Underlying - `data/<TICKER>/underlying/YYYY-MM-DD.parquet`

One row per (cycle, ticker). Key: `(cycle_id, ticker)`.

`cycle_id`, `poll_timestamp_utc`, `request_started_utc`,
`request_completed_utc`, `ticker`, `bid`, `ask`, `last`, `volume`, `open`,
`high`, `low`, `previous_close`, `trade_date`, `bid_date`, `ask_date`.

No derived values (no VWAP, no returns, no indicators).

## Contract metadata - `data/<TICKER>/metadata/YYYY-MM-DD_contracts.json`

`schema_version`, `ticker`, `trading_date`, `updated_utc`, `contract_count`,
and `contracts[]` with `symbol`, `ticker`, `expiration`, `strike`,
`option_type`, `first_discovered_utc`.

`first_discovered_utc` is the join key for stickiness analysis: it tells you
when a contract entered the universe, so you can separate "discovered at the
open" from "discovered after a move".

## Daily health - `data/health/YYYY-MM-DD.json`

`date`, `collector_started_utc`, `collector_finished_utc`, `session_start_utc`,
`session_end_utc`, `expected_cycles`, `thresholds`, `overall_error_count`,
`status` (`healthy` / `warning` / `failed`), and per ticker:
`expected_cycles`, `observed_cycles`, `coverage_pct`, `largest_gap_minutes`,
`contracts_discovered`, `tracked_contract_count`, `option_rows_saved`,
`skipped_cycles`, `batch_failures`, `errors`, `last_error`,
`last_successful_cycle`, `observed_cycle_timestamps`.

## Backup manifest - `backup/YYYY-MM-DD/manifest.json`

`trading_date`, `created_utc`, `target`, and `files[]` with `relative_path`,
`source`, `stored_at`, `sha256`, `bytes`.

## Reading tips

* Join tickers on `cycle_id`; join option rows to the underlying on
  `(cycle_id, ticker)`.
* Treat null bid/ask as market structure, not missing data.
* Never resample `tradier_*` greeks onto the minute grid - compute `model_*`
  values instead (`research/README.md`).

Related: [10 - Schemas and normalization](10-schemas-and-normalization.md), [11 - Storage](11-storage.md)
