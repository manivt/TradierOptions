# Research pipeline (future work)

This directory documents what the collected data is *for*.  No modelling code
lives in the collector: the collector produces raw, faithful observations, and
everything below happens downstream.

```text
RAW DATA
    |
    v
FEATURE ENGINEERING
    |
    v
BACKTEST DATASET
    |
    v
STRATEGY SIMULATION
    |
    v
MODEL TRAINING
    |
    v
OUT-OF-SAMPLE VALIDATION
```

## Raw data

Per ticker and trading day:

* `data/<TICKER>/options/YYYY-MM-DD.parquet` - one row per (cycle, contract).
* `data/<TICKER>/underlying/YYYY-MM-DD.parquet` - one row per (cycle, ticker).
* `data/<TICKER>/metadata/YYYY-MM-DD_contracts.json` - the sticky universe,
  including `first_discovered_utc` for every contract.

Rows from SPY, QQQ and IWM that belong to the same minute share a `cycle_id`
and a `poll_timestamp_utc`, so cross-underlying joins are exact.

## Minute-level greeks are a downstream job

Tradier greeks are stored under `tradier_*` names and carry their own vendor
timestamp (`tradier_greeks_updated_at`).  They are **not** minute-resolution
and must never be resampled, interpolated or treated as if they were sampled
at `poll_timestamp_utc`.

Locally modelled greeks should be computed later, stored under `model_*` names
(`model_iv`, `model_delta`, `model_gamma`, `model_theta`, `model_vega`) and
derived from:

* the minute option bid/ask (mid, or a microstructure-aware price),
* the underlying price from the same cycle,
* the strike,
* time to expiration measured to the actual 0DTE settlement time,
* a risk-free rate (for example the matching short-tenor Treasury yield),
* a dividend assumption for the ETF,

fed into an appropriate option-pricing model (Black-Scholes-Merton for
European-style approximations, or a binomial/trinomial model if American
early-exercise effects matter for the study).  Implied volatility is solved
from the observed price; the greeks then follow from the model.

Do not implement these here yet.

## Feature engineering notes

Features that belong downstream, never in the collector: VWAP, RSI, ATR,
MACD, moving averages, realised volatility, spread and slippage statistics,
order-flow proxies, and any signal or label.

Things worth preserving when building the backtest dataset:

* bid/ask at entry and exit, not mid, so slippage can be modelled honestly,
* `bid_size` / `ask_size` for liquidity filters,
* the gap between `poll_timestamp_utc` and `bid_date` / `ask_date`, which shows
  how stale a quote was,
* cycles where quotes are missing entirely - these are real market conditions
  for far out-of-the-money 0DTE contracts, not collector bugs.

## Validation before research

Run `validate_dataset.py` and `qa_report.py` over any period before using it
for modelling.  A day classified `SUSPICIOUS` or `PARTIAL` should be excluded
or handled explicitly rather than silently averaged in.
