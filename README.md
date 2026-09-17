# tradier-0dte-collector

Research-grade 1-minute 0DTE options data collector for SPY, QQQ and IWM,
built on the Tradier market-data API.

**This is a market-data collector only.**  There is no trading, no order
placement, no strategy logic and no modelling code.

[![tests](https://img.shields.io/badge/tests-195%20passing-brightgreen)](tests/)
[![coverage](https://img.shields.io/badge/coverage-92%25-brightgreen)](#development)
[![ruff](https://img.shields.io/badge/ruff-clean-brightgreen)](#development)
[![mypy](https://img.shields.io/badge/mypy-strict%20clean-brightgreen)](#development)

## Documentation

| Start here | For |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | agent/contributor map of the whole repository |
| [`docs/wiki/`](docs/wiki/README.md) | the knowledge base: 22 cross-linked pages |
| [`docs/wiki/02-invariants.md`](docs/wiki/02-invariants.md) | the 20 rules that must never break |
| [`docs/wiki/16-decision-log.md`](docs/wiki/16-decision-log.md) | why the design is what it is |
| [`docs/wiki/18-failure-modes.md`](docs/wiki/18-failure-modes.md) | symptom to diagnosis to action |
| [`RELEASE_REVIEW.md`](RELEASE_REVIEW.md) | release gate, deployment and QA checklists |
| [`research/README.md`](research/README.md) | the downstream research pipeline |
| [`deployment/README.md`](deployment/README.md) | e2-micro and systemd install steps |

## What it does

Every minute of the option session it:

1. samples the underlying quote and derives spot,
2. resolves today same-day (0DTE) expiration, skipping tickers that have none,
3. pulls the full same-day chain and selects the ATM +/- 10 strike window,
4. adds anything new to a **sticky intraday contract universe**,
5. quotes every tracked contract in batches,
6. writes options and underlying rows atomically into daily Parquet files.

Sticky tracking is the core design decision: once a contract is discovered it
is collected for the rest of the session, so a position opened at 09:40 still
has quotes at 15:55 even after spot has moved far away from that strike.

## Data layout

```text
data/
  SPY/options/YYYY-MM-DD.parquet
  SPY/underlying/YYYY-MM-DD.parquet
  SPY/metadata/YYYY-MM-DD_contracts.json
  QQQ/... IWM/...
  health/YYYY-MM-DD.json
```

Rows for all tickers in the same minute share `cycle_id` and
`poll_timestamp_utc`.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                        # create .venv and install deps
cp .env.example .env                           # then add TRADIER_API_TOKEN
uv run python scripts/smoke_test_tradier.py    # live read-only API check
uv run python main.py --once                   # one cycle now, any time of day
uv run python main.py --max-cycles 3           # three real boundaries
uv run python main.py                          # run until stopped
```

Note on credentials: the collector needs a **live** market-data token.  Sandbox
tokens authenticate against `https://sandbox.tradier.com/v1` and do not serve
real 0DTE chains; set `TRADIER_BASE_URL` to match whichever token you use.

## Tools

```bash
# QA over a date range
uv run python qa_report.py --ticker SPY --start-date 2026-09-01 \
    --end-date 2026-09-15 --data-dir ./data --csv qa.csv

# Prove sticky tracking and cycle alignment for one day
uv run python validate_dataset.py --ticker SPY --date 2026-09-15

# Silent-failure check (exit 0 healthy, 1 warning, 2 fatal)
uv run python watchdog.py

# Backups with SHA-256 checksums
uv run python backup.py --all-complete-days
```

## Configuration

All settings come from `.env`; see `.env.example`.  `TRADIER_API_TOKEN` is
required (`TRADIER_API_KEY` is accepted as an alias for existing setups).
Configuration is validated at startup and secrets are never logged.

## Timestamps

* Everything persisted is UTC.
* `poll_timestamp_utc` is **our** intended sampling boundary, never a Tradier
  timestamp.
* Vendor timestamps are preserved separately: `trade_date`, `bid_date`,
  `ask_date`, `tradier_greeks_updated_at`.

## Tradier greeks

Vendor greeks are stored verbatim under `tradier_*` names with their own
timestamp.  They are not minute-resolution data, are never interpolated, and
must never be confused with locally modelled greeks, which will use `model_*`
names (see `research/README.md`).

## Assumptions about Tradier response fields

* `trade_date`, `bid_date`, `ask_date` are epoch **milliseconds**; `0` means
  "no trade/quote yet" and is stored as null.
* `greeks.updated_at` is a naive datetime string interpreted as **UTC**.
* Container keys (`quotes.quote`, `options.option`, `expirations.date`) may be
  null, a single object or a list; all three shapes are normalised in the
  client.
* Numeric fields may arrive as strings and are coerced defensively.
* `bidsize` / `asksize` are contract counts and are stored as nullable int64.
* Missing greeks outside market hours are normal, not an error.

## Early closes

On NYSE half days the equity market closes at 13:00 ET. Cboe's 2026 schedule
lists a 13:00 ET close for BZX, C2 and EDGX Options, while Cboe C1 materials
list 13:15 ET. Because SPY, QQQ and IWM are multiply listed and Tradier returns
consolidated quotes, the collector uses an explicit, configurable policy:

```text
early-close option session end = XNYS equity close + EARLY_CLOSE_OPTION_EXTRA_MINUTES
```

Default 15 minutes, preserving the C1 window; set it to 0 to stop at 13:00 ET.
See `tradier_collector/market_clock.py` and `RELEASE_REVIEW.md`.

## Development

```bash
uv run pytest -q          # 195 tests
uv run pytest --cov       # 92% coverage
uv run ruff check .
uv run mypy .
```

The test suite never touches the network.  Dependencies are managed with
`uv add` / `uv add --dev` (`pyproject.toml` + `uv.lock`); there is no
`requirements.txt`.

Before changing anything, read [`CLAUDE.md`](CLAUDE.md) and
[`docs/wiki/02-invariants.md`](docs/wiki/02-invariants.md).

## Status

All offline checks pass and the restart/data-integrity paths have been reviewed
and covered by tests.  The live API path has not been exercised from the
development machine: run the smoke test and a bounded `--max-cycles` run on the
target VM before enabling the service.  Open question: the option session end on
NYSE half days, see [`docs/wiki/05-market-clock.md`](docs/wiki/05-market-clock.md).
