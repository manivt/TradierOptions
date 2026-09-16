# 12 - Health, QA, watchdog

[Index](README.md) | Prev: [11 - Storage](11-storage.md) | Next: [13 - Validation and backup](13-validation-and-backup.md)

Code: `tradier_collector/health.py`, `qa_report.py`, `watchdog.py`
Tests: `tests/test_health.py` (10), `tests/test_qa_report.py` (13), `tests/test_watchdog.py` (14)

Three layers answer three different questions:

| Layer | Question | Runs |
| --- | --- | --- |
| `HealthTracker` | what did the collector itself observe today? | in-process |
| `qa_report.py` | is this date range actually complete and trustworthy? | ad hoc, from files |
| `watchdog.py` | is the collector silently dead right now? | separate process |

The watchdog must be separate: a hung or crashed collector cannot report its own
failure, so the check reads only what is on disk.

## HealthTracker

Accumulates per-ticker counters and writes `data/health/YYYY-MM-DD.json` every 5
cycles plus once at session end (`finished=True` stamps
`collector_finished_utc`).

Per ticker: `expected_cycles`, `observed_cycles`, `coverage_pct`,
`largest_gap_minutes`, `contracts_discovered`, `tracked_contract_count`,
`option_rows_saved`, `skipped_cycles`, `batch_failures`, `errors`, `last_error`,
`last_successful_cycle`, plus `observed_cycle_timestamps`.

Design notes:

* A cycle counts as observed only when the ticker succeeded; an errored cycle
  increments `errors` instead. A skip (no 0DTE) counts as observed and is also
  tallied in `skipped_cycles`, because the collector did its job.
* `observed_cycle_timestamps` is persisted so `restore()` can rebuild exact gap
  history after a restart - without it, a collector restarted at noon would
  report the morning as one giant gap.
* `restore()` ignores a damaged or wrong-day file (logging it) rather than
  failing startup.
* Status: `healthy >= HEALTHY_COVERAGE_PCT`, `warning >= WARNING_COVERAGE_PCT`,
  else `failed`, taken over the **worst** ticker.

## qa_report.py

`analyze_day` compares observations against
`MarketClock.expected_poll_timestamps(day)` - the explicit list, never an
estimate (I17). It reports coverage, missing/unexpected cycles, largest gap,
first/last observation, unique symbols, median/min/max rows per cycle,
duplicates, bid/ask/two-sided availability, cycles with zero option rows, cycles
where every contract was null on both sides, and the underlying metrics.

Classification (`_classify`):

* `MISSING` - no observations at all.
* `PARTIAL` - coverage below the warning threshold (or below healthy with no
  other anomaly).
* `SUSPICIOUS` - coverage is fine but something smells: duplicates, all-null
  cycles, underlying cycles with no options, observations off the expected
  boundaries, a cycle with fewer than half the median rows, missing metadata, or
  weak underlying coverage. Each reason is appended to `notes`.
* `COMPLETE` - none of the above.

CLI: `--ticker` (repeatable or comma separated), `--start-date`, `--end-date`,
`--data-dir`, `--csv`. Exit 0 only when every day is `COMPLETE`, so it can gate
a cron job. It works without a valid token (see
[03 - Configuration](03-configuration.md), offline fallback).

## watchdog.py

`check_day(settings, day, now=..., max_silence_minutes=10)` checks, in order:
not a trading day or before the open (healthy, nothing to do); health file
exists and parses; a finished session recorded `collector_finished_utc`;
per-ticker coverage against both thresholds; during a live session, how long ago
the last successful cycle was; recorded error counts; the collector's own
`status` field.

Exit codes: `0` healthy, `1` warning, `2` fatal/missing - designed for
`systemctl list-units --failed` and external monitoring.

Related: [06 - Scheduler](06-scheduler.md), [18 - Failure modes](18-failure-modes.md)
