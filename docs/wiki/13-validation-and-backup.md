# 13 - Validation and backup

[Index](README.md) | Prev: [12 - Health, QA, watchdog](12-health-qa-watchdog.md) | Next: [14 - Testing](14-testing.md)

Code: `validate_dataset.py`, `backup.py` | Tests: `tests/test_validate_and_backup.py` (8)

## validate_dataset.py - proving the dataset properties

QA answers "is the day complete". Validation answers "does the data have the
structural properties a backtest depends on". It is the runtime counterpart of
[02 - Invariants](02-invariants.md).

```bash
uv run python validate_dataset.py --ticker SPY --date 2026-09-15 --data-dir ./data
```

Checks, each printed as PASS/FAIL with evidence:

1. option, underlying and metadata artefacts present;
2. **sticky contracts keep reporting all session** - samples up to five
   contracts discovered in the first 30 minutes and requires each to still have
   observations within 5 minutes of the final cycle;
3. **out-of-window contracts still collected late** - counts observations in the
   last 30 minutes for contracts more than `strike_spacing * N` away from the
   closing spot. Those rows can only exist because tracking is sticky, so this
   check is the direct demonstration asked for in the brief;
4. observations align with the intended poll boundaries;
5. exactly one poll timestamp per `cycle_id`;
6. no duplicate `(cycle_id, symbol)` rows;
7. quote coverage (bid %, ask %, two-sided %), with a note when two-sided drops
   below 50% - expected for deep out-of-the-money 0DTE late in the day;
8. `tradier_greeks_updated_at` is distinct from `poll_timestamp_utc`, so vendor
   greeks can never be mistaken for minute-resolution data.

Exit 0 when every check passes.

## backup.py - checksummed copies

```bash
uv run python backup.py --date 2026-09-15
uv run python backup.py --all-complete-days --backup-dir ./backup
```

Backs up, per day: both Parquet files per ticker, the contract metadata JSON and
the daily health JSON. Every file gets a SHA-256 digest, and a `manifest.json`
records paths, digests and sizes next to the copies.

Safety rule: a day is only copied once its session window has ended
(`day_is_complete`). Copying a file that is still being rewritten every minute
would produce a backup that looks complete but is a point-in-time snapshot of an
unfinished day. `--force` overrides this deliberately.

Extensibility: `BackupTarget` is a `Protocol` with `store()` and `describe()`.
`LocalBackupTarget` writes to a directory; adding Google Cloud Storage later
means implementing that one interface and nothing else. GCS was deliberately not
implemented (decision D11).

Related: [08 - Sticky universe](08-sticky-universe.md), [15 - Deployment and operations](15-deployment-operations.md)
