# 00 - Architecture

[Index](README.md) | Next: [01 - Domain primer](01-domain-primer.md)

## Purpose

Collect 1-minute SPY/QQQ/IWM 0DTE option and underlying quotes for later
backtesting, slippage analysis and model training.  **Collection only**: no
orders, no strategy, no modelled greeks.

## Component graph

```text
Tradier REST API
    |
    v
TradierClient .................. tradier_collector/tradier_client.py
    |  bounded retries, shape normalisation, token never leaked
    v
ExpirationResolver ............. tradier_collector/expiration.py
    |  is there a same-day expiration for this ticker?
    v
discover_window ................ tradier_collector/discovery.py
    |  full chain -> select_atm_window (strike_selector.py)
    v
ContractTracker ................ tradier_collector/contract_tracker.py
    |  STICKY universe, persisted JSON, restart-safe
    v
TickerCollector ................ tradier_collector/collector.py
    |  batched quotes, per-ticker failure isolation -> TickerResult
    v
run_cycle ...................... tradier_collector/cycle.py
    |  one cycle_id + poll_timestamp_utc shared by all tickers
    v
normalize_* .................... tradier_collector/normalization.py
    |  canonical rows; vendor fields preserved verbatim
    v
OPTION_SCHEMA / UNDERLYING_SCHEMA  tradier_collector/schemas.py
    |  fixed PyArrow schemas, no pandas inference
    v
append_*_snapshot .............. tradier_collector/storage.py
    |  read -> merge -> dedupe -> validate -> tmp write -> os.replace
    v
HealthTracker .................. tradier_collector/health.py
       data/health/YYYY-MM-DD.json

CollectorScheduler ............. scheduler.py   (drives the above on boundaries)
MarketClock .................... tradier_collector/market_clock.py (when to run)
```

## Out-of-process tools

| Tool | Runs as | Answers |
| --- | --- | --- |
| `watchdog.py` | cron / systemd timer | Is the collector silently dead? |
| `qa_report.py` | ad hoc | Is a date range complete and trustworthy? |
| `validate_dataset.py` | ad hoc | Do the dataset invariants hold for one day? |
| `backup.py` | cron | Are finished days copied with checksums? |
| `scripts/smoke_test_tradier.py` | manual | Does the live API work with this token? |

The watchdog is deliberately a separate process: a hung or crashed collector
cannot report its own failure, so the check reads only artefacts on disk.

## Data layout

```text
data/
  <TICKER>/options/YYYY-MM-DD.parquet        one row per (cycle, contract)
  <TICKER>/underlying/YYYY-MM-DD.parquet     one row per (cycle, ticker)
  <TICKER>/metadata/YYYY-MM-DD_contracts.json  sticky universe + discovery times
  health/YYYY-MM-DD.json                     daily coverage and status
logs/collector.log                           7 daily rotating files
backup/YYYY-MM-DD/...                        checksummed copies + manifest.json
```

## Layering rule

`config.py` depends on nothing in the package.  `tradier_collector/*` depends
on `config` only for the `Settings` type (`market_clock.py` avoids even that by
using a `ClockSettings` Protocol).  Top-level scripts depend on both.  There are
no cycles, which is why `market_clock` can be imported by QA tools that never
touch the API.

Related: [02 - Invariants](02-invariants.md), [09 - Collection cycle](09-collection-cycle.md),
[16 - Decision log](16-decision-log.md)
