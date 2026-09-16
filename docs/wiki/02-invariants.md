# 02 - Invariants

[Index](README.md) | Prev: [01 - Domain primer](01-domain-primer.md) | Next: [03 - Configuration](03-configuration.md)

Rules that must hold.  Each lists where it is enforced and where it is tested.
**Breaking any of these silently corrupts the research dataset.**

| # | Invariant | Enforced in | Tested in |
| --- | --- | --- | --- |
| I1 | A tracked contract is never dropped during a trading day. | `contract_tracker.ContractTracker` (no removal API exists) | `test_contract_tracker.py`, `test_collector.py::test_sticky_universe_survives_spot_moving_away` |
| I2 | The sticky universe survives a process restart. | `ContractTracker.load` plus `save` after every change | `test_contract_tracker.py::test_universe_is_persisted_and_restored_after_restart` |
| I3 | `poll_timestamp_utc` is our intended boundary, never a Tradier timestamp. | `cycle.run_cycle` passes the scheduler boundary down | `test_scheduler.py::test_polls_land_on_absolute_boundaries` |
| I4 | All tickers in a cycle share one `cycle_id` and one poll timestamp. | `collector.CycleContext`, created once per cycle | `test_cycle.py::test_every_ticker_shares_one_cycle_id_and_timestamp` |
| I5 | Vendor greeks are stored verbatim, never interpolated or renamed. | `normalization.GREEK_FIELDS`, `schemas.OPTION_SCHEMA` | `test_normalization.py::test_complete_option_record` |
| I6 | Persisted dtypes never drift, including all-null columns. | explicit `pa.Schema` on every write (`storage.rows_to_table`) | `test_storage.py::test_null_values_do_not_change_the_persisted_schema` |
| I7 | A Parquet file is never partially overwritten. | temp file in the same directory plus `os.replace` | `test_storage.py::test_write_failure_preserves_the_existing_file` |
| I8 | No duplicate `(cycle_id, symbol)` or `(cycle_id, ticker)` rows. | `storage._dedupe_keep_last` | `test_storage.py::test_duplicate_cycle_and_symbol_is_replaced_not_duplicated` |
| I9 | One ticker failing never affects another. | `TickerCollector.collect` catches everything into `TickerResult` | `test_cycle.py::test_one_ticker_failing_does_not_stop_the_others` |
| I10 | Retries are bounded (max 3), never unbounded. | `TradierClient._request`, `http_max_attempts` | `test_tradier_client.py::test_retries_are_bounded` |
| I11 | The API token never reaches logs, exceptions or stdout. | `Settings.redacted_summary`, `tradier_client.sanitize_body` | `test_config.py::test_secrets_are_never_rendered` |
| I12 | Scheduling uses absolute boundaries; processing time never accumulates. | `MarketClock.next_boundary`, recomputed each cycle | `test_scheduler.py::test_no_cumulative_drift_when_cycles_are_slow` |
| I13 | An overrun skips boundaries instead of firing catch-up bursts. | `CollectorScheduler.run` recomputes from `now` | `test_scheduler.py::test_overrun_skips_a_boundary_instead_of_catching_up` |
| I14 | A missing 0DTE expiration is a skip, not a crash. | `ExpirationResolver.zero_dte` returns `None` | `test_collector.py::test_no_zero_dte_skips_options_but_keeps_the_underlying` |
| I15 | Corrupt metadata is preserved, never silently overwritten. | `ContractTracker._handle_corrupt` quarantines to `.corrupt-<ts>` | `test_contract_tracker.py::test_corrupt_metadata_is_quarantined_not_overwritten` |
| I16 | A failed quote batch does not discard the successful ones. | `TickerCollector._collect_quotes` continues after a failure | `test_collector.py::test_one_batch_failure_keeps_the_rest` |
| I17 | Expected cycle counts are explicit timestamps, never an estimate. | `MarketClock.expected_poll_timestamps` | `test_market_clock.py`, `test_qa_report.py` |
| I18 | Session bounds are DST- and holiday-correct. | local clock time attached to a local date, then converted to UTC | `test_market_clock.py::test_dst_transition_days_keep_local_clock_times` |
| I19 | No indicators, signals or strategy logic in the collector. | structural: schemas contain raw fields only | reviewed by hand |
| I20 | The test suite never touches the network. | every client is faked in `tests/` | the whole suite runs offline |

## How to check them quickly

```bash
uv run pytest -q          # 195 tests, all offline
uv run python validate_dataset.py --ticker SPY --date <YYYY-MM-DD>
uv run python qa_report.py --ticker SPY --start-date <d> --end-date <d>
```

`validate_dataset.py` is the runtime counterpart of this page: it re-checks
I1, I3, I4, I5 and I8 against real collected data rather than against fakes.

Related: [16 - Decision log](16-decision-log.md), [18 - Failure modes](18-failure-modes.md)
