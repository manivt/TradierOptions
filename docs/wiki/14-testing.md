# 14 - Testing

[Index](README.md) | Prev: [13 - Validation and backup](13-validation-and-backup.md) | Next: [15 - Deployment and operations](15-deployment-operations.md)

195 tests, all offline. `uv run pytest -q` | coverage 92% | `ruff check .` clean |
`mypy .` clean under strict mode.

## Layout

| File | Tests | Focus |
| --- | --- | --- |
| `conftest.py` | - | `settings` fixture (tmp dirs) and quote/chain builders |
| `test_config.py` | 17 | defaults, invalid values, missing token, secret redaction |
| `test_tradier_client.py` | 25 | shapes, retries, statuses, invalid JSON, sanitisation |
| `test_strike_selector.py` | 11 | ATM, ties, truncation, malformed entries, half-dollar ladders |
| `test_normalization.py` | 13 | complete/partial records, timestamps, dtype stability |
| `test_expiration.py` | 7 | 0DTE present/absent, caching, day rollover, error propagation |
| `test_contract_tracker.py` | 11 | stickiness, persistence, restart, corruption, new day |
| `test_storage.py` | 14 | append, dedupe, nulls, atomicity, schema drift, temp cleanup |
| `test_collector.py` | 14 | batching, partial failures, missing symbols, restart, isolation |
| `test_cycle.py` | 8 | aggregation, one/all tickers failing, shared cycle identity |
| `test_market_clock.py` | 15 | weekends, holidays, DST, early closes, boundaries |
| `test_scheduler.py` | 12 | drift, overrun, pre-open/post-close, shutdown, health restart |
| `test_health.py` | 10 | coverage maths, thresholds, gaps, restore |
| `test_qa_report.py` | 13 | synthetic days with deliberate defects |
| `test_watchdog.py` | 14 | every exit-code path |
| `test_validate_and_backup.py` | 8 | sticky proof on collected data, checksums, guards |
| `test_logging_setup.py` | 3 | file plus stdout handlers, no duplication, retention |

## Fakes, not HTTP mocks

* `FakeSession` / `FakeResponse` (`test_tradier_client.py`) replay a scripted
  list of responses or exceptions, and the client's `sleep` hook records delays
  instead of waiting - so retry and backoff behaviour is asserted exactly and
  the suite stays fast.
* `FakeClient` (`test_collector.py`) implements the four client methods over a
  synthetic chain, with knobs for spot, expirations, failing batch indexes and
  dropped symbols. Other test modules import it, so one fake stays authoritative.
* `FakeTime` (`test_scheduler.py`) is a virtual clock whose `sleep` advances
  time, so a full session of scheduling is exercised in milliseconds.

## What the tricky tests actually prove

* `test_no_cumulative_drift_when_cycles_are_slow` - 10s of work per ticker for 5
  cycles still yields exactly 60s spacing.
* `test_overrun_skips_a_boundary_instead_of_catching_up` - 45s per ticker yields
  gaps of at least 120s, all on :00 boundaries, and never a burst.
* `test_sticky_universe_survives_spot_moving_away` - after a rally, the morning
  symbols are a strict subset of the tracked set and still appear in the later
  cycle's rows.
* `test_universe_is_restored_after_a_restart` - a brand-new collector with an
  empty chain still quotes 42 contracts, which is only possible from disk state.
* `test_write_failure_preserves_the_existing_file` - a patched `write_table`
  raises; the original bytes are unchanged and no temp files remain.
* `test_restart_mid_session_preserves_morning_counters` - two scheduler runs two
  hours apart produce `observed_cycles == 4`, not 2.

## Conventions

* No network, no sleeping, no reliance on the current date: trading dates are
  fixed (2026-09-15 is a Tuesday; 2026-11-27 is an early close).
* `tests/__init__.py` exists so mypy resolves `tests.conftest` unambiguously.
* Deliberately thin: argparse wiring for the `backup.py` and `watchdog.py` main
  functions is not directly tested (their logic is, via `backup_day` and
  `check_day`) - see [19 - Limitations](19-limitations-and-future-work.md).
