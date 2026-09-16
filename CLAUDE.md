# CLAUDE.md - agent map for tradier-0dte-collector

Read this first, then the wiki page for whatever you are about to touch.
The knowledge base lives in [`docs/wiki/`](docs/wiki/README.md); this file is
its index and the working agreement for changing this repository.

## What this project is

A 1-minute 0DTE options data collector for SPY, QQQ and IWM on the Tradier API,
producing research-grade daily Parquet files for later backtesting and
modelling. Runs continuously on a Google Cloud e2-micro under systemd.

**Collection only.** No trading, no order placement, no strategy logic, no
machine-learning models, no indicators. If a request implies adding any of
those, it belongs in a separate package that reads `data/`.

## Before you change anything

1. Read [`docs/wiki/02-invariants.md`](docs/wiki/02-invariants.md). Twenty
   invariants (I1-I20) with their enforcement points and tests. Do not break one
   without an explicit instruction and a matching update to the decision log.
2. Read [`docs/wiki/16-decision-log.md`](docs/wiki/16-decision-log.md) for the
   reasoning behind the design (D1-D22) and the cost of reversing each choice.
3. Run `uv run pytest -q` (195 tests, offline) to confirm a clean baseline.

## Wiki map

| Read this | When you are working on |
| --- | --- |
| [00 - Architecture](docs/wiki/00-architecture.md) | anything; the component graph and data flow |
| [01 - Domain primer](docs/wiki/01-domain-primer.md) | 0DTE, ATM windows, OCC symbols, cycles |
| [02 - Invariants](docs/wiki/02-invariants.md) | **always** |
| [03 - Configuration](docs/wiki/03-configuration.md) | settings, `.env`, validation, secrets |
| [04 - Tradier API client](docs/wiki/04-tradier-client.md) | HTTP, retries, rate limits, response shapes |
| [05 - Market clock](docs/wiki/05-market-clock.md) | sessions, DST, holidays, early closes, boundaries |
| [06 - Scheduler](docs/wiki/06-scheduler.md) | the run loop, signals, restarts, `main.py` |
| [07 - Expiration and discovery](docs/wiki/07-expiration-and-discovery.md) | 0DTE resolution, ATM +/- N selection |
| [08 - Sticky universe](docs/wiki/08-sticky-universe.md) | **the core design decision**; contract tracking |
| [09 - Collection cycle](docs/wiki/09-collection-cycle.md) | per-ticker flow, batching, failure isolation |
| [10 - Schemas and normalization](docs/wiki/10-schemas-and-normalization.md) | fields, dtypes, vendor greeks |
| [11 - Storage](docs/wiki/11-storage.md) | Parquet writes, atomicity, dedupe, schema drift |
| [12 - Health, QA, watchdog](docs/wiki/12-health-qa-watchdog.md) | coverage accounting, silent-failure detection |
| [13 - Validation and backup](docs/wiki/13-validation-and-backup.md) | proving stickiness, checksummed copies |
| [14 - Testing](docs/wiki/14-testing.md) | the suite, the fakes, what is actually proven |
| [15 - Deployment and operations](docs/wiki/15-deployment-operations.md) | e2-micro, systemd, runbook |
| [16 - Decision log](docs/wiki/16-decision-log.md) | why the design is what it is |
| [17 - Data dictionary](docs/wiki/17-data-dictionary.md) | every persisted field and artefact |
| [18 - Failure modes](docs/wiki/18-failure-modes.md) | a symptom in the logs or QA output |
| [19 - Limitations](docs/wiki/19-limitations-and-future-work.md) | scope boundaries, open questions |
| [20 - Build history](docs/wiki/20-build-history.md) | how it was built, and the dead ends |

Other documents: [`README.md`](README.md) (user-facing),
[`RELEASE_REVIEW.md`](RELEASE_REVIEW.md) (release gate, checklists),
[`research/README.md`](research/README.md) (downstream pipeline, model greeks),
[`deployment/README.md`](deployment/README.md) (full install steps).

## Code map

```text
config.py                    typed Settings, validated once at startup
main.py                      CLI entry point (--once, --max-cycles, --log-level)
scheduler.py                 CollectorScheduler: absolute wall-clock boundaries
qa_report.py                 range QA -> COMPLETE/PARTIAL/MISSING/SUSPICIOUS
watchdog.py                  separate-process health check, exit 0/1/2
validate_dataset.py          proves sticky tracking and alignment on real data
backup.py                    checksummed copies of finished days
tradier_collector/
  tradier_client.py          HTTP, bounded retries, shape normalisation
  market_clock.py            XNYS sessions, DST, early closes, poll boundaries
  expiration.py              0DTE resolution with per-day cache
  strike_selector.py         ATM +/- N window, deterministic tie-breaking
  contract_tracker.py        the sticky universe (persisted, restart-safe)
  discovery.py               chain -> window -> tracker
  collector.py               per-ticker cycle, TickerResult, failure isolation
  cycle.py                   cycle identity and aggregation
  normalization.py           raw Tradier JSON -> canonical rows
  schemas.py                 fixed PyArrow schemas (the data contract)
  storage.py                 atomic daily Parquet, dedupe, loaders
  health.py                  daily health accounting and JSON artefact
  logging_setup.py           stdout plus a 7-day rotating file
scripts/smoke_test_tradier.py  live read-only check against the real API
tests/                       195 offline tests
deployment/                  systemd units with __PLACEHOLDER__ values
```

## Working agreement

* **Dependencies**: `uv add` / `uv add --dev` only. No `pip install`, no
  `requirements.txt`.
* **Commands**: `uv run pytest -q`, `uv run pytest --cov`, `uv run ruff check .`,
  `uv run mypy .`. All four must stay clean; do not disable tests or weaken
  typing to make them pass.
* **Secrets**: never print, log or embed the token; `.env` is never committed.
  New logging of settings goes through `Settings.redacted_summary()`.
* **Timestamps**: persist UTC; use New York only for dates, session bounds and
  the 0DTE lookup. Never derive `poll_timestamp_utc` from a vendor field.
* **Vendor greeks**: keep `tradier_*` verbatim; future modelled values use
  `model_*`. Never interpolate or replace vendor values.
* **Schemas**: any new column goes into `schemas.py` first; never rely on pandas
  inference for persisted files.
* **Failure handling**: no silent excepts. One ticker failing must never affect
  another; record it in `TickerResult` and log with a traceback.
* **Tests**: offline always. Extend the existing fakes (`FakeClient`,
  `FakeSession`, `FakeTime`) rather than adding new HTTP mocking.
* **Docs**: a change that alters behaviour updates the relevant wiki page, and a
  design change adds a row to the decision log.

## Fast answers

* Where is the sticky rule enforced? `tradier_collector/contract_tracker.py`;
  there is no removal API, by design.
* Why not `collect(); sleep(60)`? Drift - see D6 and
  [06 - Scheduler](docs/wiki/06-scheduler.md).
* Why is a null bid not a bug? 0DTE market structure - see
  [01 - Domain primer](docs/wiki/01-domain-primer.md).
* What is unresolved? The early-close option session end - see D14 and
  [19 - Limitations](docs/wiki/19-limitations-and-future-work.md).
