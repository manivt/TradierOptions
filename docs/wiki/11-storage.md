# 11 - Storage

[Index](README.md) | Prev: [10 - Schemas and normalization](10-schemas-and-normalization.md) | Next: [12 - Health, QA, watchdog](12-health-qa-watchdog.md)

Code: `tradier_collector/storage.py` | Tests: `tests/test_storage.py` (14)

## Public API

```python
append_option_snapshot(rows, *, data_dir, ticker, day) -> int
append_underlying_snapshot(rows, *, data_dir, ticker, day) -> int
load_option_day(data_dir, ticker, day) -> pd.DataFrame
load_underlying_day(data_dir, ticker, day) -> pd.DataFrame
option_path / underlying_path / metadata_path / health_path
ensure_layout(data_dir, tickers)
clean_stale_temp_files(directory, max_age_seconds=3600) -> int
```

## Write algorithm

```text
rows -> table (explicit schema)
  -> clean stale temp files
  -> read existing file (if any) and conform it to the schema
  -> concat
  -> dedupe keeping the LAST row per key
  -> sort by (poll_timestamp_utc, symbol | ticker)
  -> conform again
  -> write temp file in the SAME directory
  -> os.replace(temp, final)
```

`os.replace` is atomic only within one filesystem, which is why the temp file is
created as a sibling of the target (`test_replacement_is_atomic_via_os_replace`
asserts the parent directories match). If anything raises, the temp file is
cleaned up and the previous good file is left exactly as it was (I7).

`_dedupe_keep_last` builds a key-to-index map in one pass and keeps the newest
observation per key, so re-running a cycle corrects rather than duplicates (I8).

`_conform` casts an existing file to the canonical schema and raises
`SchemaDriftError` when columns are missing or unexpected - deliberately
refusing to write rather than reshaping someone else's data (I6).

Temp-file cleanup is conservative: only files matching this module's
`.tmp-<pid>-<uuid>` pattern, and only when older than an hour, so a concurrent
writer is never disturbed.

## Cost and the documented scaling limit

Read-modify-rewrite means the cost of a cycle grows with the size of the day's
file: roughly tens of milliseconds per ticker near the close (about 17k rows).
At one cycle per minute that is irrelevant; the module docstring records that a
materially higher frequency (sub-second sampling, or dozens of underlyings)
should move to an append-only partitioned layout with end-of-day compaction, or
an embedded database such as DuckDB. The change stays inside this module.

Practical consequence for tests: fixtures that simulate a whole day must write
in one batch, not 406 times, or they become quadratic. `tests/test_qa_report.py`
carries a comment saying exactly that.

## Reading

`load_*_day` returns an empty DataFrame **with all schema columns** when the
file is absent, so downstream code never branches on existence. Reads conform to
the schema on the way out, so a hand-edited file is caught at load time.

Related: [02 - Invariants](02-invariants.md) I6/I7/I8, [17 - Data dictionary](17-data-dictionary.md)
