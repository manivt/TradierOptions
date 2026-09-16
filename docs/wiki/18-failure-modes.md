# 18 - Failure modes

[Index](README.md) | Prev: [17 - Data dictionary](17-data-dictionary.md) | Next: [19 - Limitations](19-limitations-and-future-work.md)

Symptom to diagnosis to action. Most of these are *designed* behaviours, not
bugs - the table says which.

| Symptom | Diagnosis | Designed? | Action |
| --- | --- | --- | --- |
| Log: `No 0DTE expiration for X` | That ticker has no same-day listing today | yes | None. Underlying still collected |
| Log: `ATM window truncated at ... edge` | Spot near the end of the listed strike ladder | yes | None, unless persistent - then raise `DISCOVERY_STRIKES_EACH_SIDE` |
| Log: `N tracked contracts missing from the quote response` | Tradier omitted symbols from a batch | yes | None if sporadic; investigate if it grows through the day |
| Log: `Quote batch failed ... continuing` | One batch errored; the rest still persisted | yes | Watch `batch_failures` in the health file |
| Log: `Cycle ... overran its interval` | A cycle took longer than the poll interval | yes | Fine occasionally; if frequent, raise `DISCOVERY_INTERVAL_CYCLES` or the interval |
| Log: `Tradier rate-limit headers` warnings | Approaching or hitting 429 | partly | Raise `DISCOVERY_INTERVAL_CYCLES`, or reduce tickers |
| Repeated HTTP 401 and every ticker failing | Token expired or lost market-data entitlement | no | Replace the token in `.env`, restart the service |
| Watchdog exit 2, `health file missing` | Collector never started today, or `DATA_DIR` is wrong | no | `systemctl status`, check `.env` paths |
| Watchdog exit 2, `last successful cycle was N minutes ago` | Collector hung or lost network | no | Restart the service; check egress and the journal |
| Watchdog exit 2, `collector never recorded a finish time` | Process ended before the session close | no | Check for OOM or a hard stop; `TimeoutStopSec` must allow a graceful stop |
| QA `PARTIAL` | Coverage below the warning threshold | no | Compare `first_observation` and `largest_gap_minutes`; usually a late start or a restart gap |
| QA `SUSPICIOUS` with `cycles with an underlying quote but no options` | Options failed while the underlying succeeded | no | Look for chain or batch errors in the journal for those minutes |
| QA `SUSPICIOUS` with `thin cycle` | A cycle returned far fewer rows than the median | no | Usually a partially failed batch; check `batch_failures` |
| QA `SUSPICIOUS` with duplicate rows | Should be impossible via the storage layer | no | Suspect a hand-edited file or an external writer |
| `SchemaDriftError` on write | The existing daily file does not match the canonical schema | no | Inspect the file; the collector refuses to write rather than reshape it |
| A `.corrupt-<timestamp>` metadata file appears | Metadata JSON was damaged, for example by power loss mid-write | yes | Inspect the quarantined file; the day continues with a fresh universe |
| Two-sided quote percentage low late in the day | Real 0DTE market structure | yes | None - do not treat it as a collector fault |
| `validate_dataset.py` fails the sticky check | A morning contract stopped reporting | no | Serious: check for an unintended universe reset or a mid-session data-dir change |
| Exit code 2 from `main.py` | Configuration error | no | The message names the offending variable |

## Triage order for "something is wrong today"

1. `python watchdog.py` - is it alive and covered?
2. `journalctl -u tradier-collector.service --since today | grep -E "ERROR|failed"`.
3. `python qa_report.py --ticker SPY --start-date <day> --end-date <day>`.
4. `python validate_dataset.py --ticker SPY --date <day>` for structural doubts.
5. Only then read the Parquet files directly.

Related: [12 - Health, QA, watchdog](12-health-qa-watchdog.md), [15 - Deployment and operations](15-deployment-operations.md)
