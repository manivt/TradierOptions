# LLM Wiki: tradier-0dte-collector

A durable knowledge base for this repository.  Each page is self-contained,
states *what exists*, *why it is that way*, and *what would break if changed*.
Pages reference code as `path/file.py::symbol` (symbol names, not line numbers,
so the references survive edits).

Root map for agents: [`CLAUDE.md`](../../CLAUDE.md).

## Read in this order (cold start)

1. [00 - Architecture](00-architecture.md) - components and the data flow.
2. [01 - Domain primer](01-domain-primer.md) - 0DTE, OCC symbols, ATM windows.
3. [02 - Invariants](02-invariants.md) - the rules that must never break.
4. [16 - Decision log](16-decision-log.md) - why each choice was made.

## Component pages

| Page | Covers | Primary code |
| --- | --- | --- |
| [03 - Configuration](03-configuration.md) | typed settings, validation, secrets | `config.py` |
| [04 - Tradier API client](04-tradier-client.md) | HTTP, retries, response shapes | `tradier_collector/tradier_client.py` |
| [05 - Market clock](05-market-clock.md) | sessions, DST, early closes, boundaries | `tradier_collector/market_clock.py` |
| [06 - Scheduler](06-scheduler.md) | drift-free loop, signals, restarts | `scheduler.py`, `main.py` |
| [07 - Expiration and discovery](07-expiration-and-discovery.md) | 0DTE resolution, ATM +/- N | `expiration.py`, `strike_selector.py`, `discovery.py` |
| [08 - Sticky universe](08-sticky-universe.md) | the core design decision | `contract_tracker.py` |
| [09 - Collection cycle](09-collection-cycle.md) | per-ticker flow, failure isolation | `collector.py`, `cycle.py` |
| [10 - Schemas and normalization](10-schemas-and-normalization.md) | canonical rows, vendor greeks | `schemas.py`, `normalization.py` |
| [11 - Storage](11-storage.md) | atomic Parquet, dedupe, schema drift | `storage.py` |
| [12 - Health, QA, watchdog](12-health-qa-watchdog.md) | coverage accounting, silent failures | `health.py`, `qa_report.py`, `watchdog.py` |
| [13 - Validation and backup](13-validation-and-backup.md) | proving stickiness, checksummed copies | `validate_dataset.py`, `backup.py` |
| [14 - Testing](14-testing.md) | suite layout, fakes, what is proven | `tests/` |
| [15 - Deployment and operations](15-deployment-operations.md) | e2-micro, systemd, runbook | `deployment/` |

## Reference pages

* [16 - Decision log](16-decision-log.md) - numbered decisions with rationale and consequences.
* [17 - Data dictionary](17-data-dictionary.md) - every persisted column and artefact.
* [18 - Failure modes](18-failure-modes.md) - symptom to diagnosis to fix.
* [19 - Limitations and future work](19-limitations-and-future-work.md) - what is deliberately not done.
* [20 - Build history](20-build-history.md) - how the implementation was carried out.

## One-paragraph summary

The system samples SPY, QQQ and IWM 0DTE option chains once per minute during
the option session and writes research-grade daily Parquet files.  Its defining
property is the **sticky intraday contract universe**: once a contract enters
the ATM +/- 10 discovery window it is quoted for the rest of the session, so a
position opened in the morning still has quotes at the close.  Everything else
in the design serves correctness, recoverability and integrity: fixed PyArrow
schemas, atomic writes, absolute wall-clock scheduling, per-ticker failure
isolation, and restart-safe state on disk.  It collects data only - no trading,
no strategy, no models.
