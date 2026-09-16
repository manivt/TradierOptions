# Release review

## Architecture summary

```text
Tradier API
  -> TradierClient        persistent session, bounded retries, shape normalisation
  -> ExpirationResolver   per-day cached 0DTE resolution
  -> discovery            full chain -> ATM +/- N strike window
  -> ContractTracker      sticky intraday universe, atomic JSON, restart-safe
  -> TickerCollector      per-ticker cycle, all failures contained in a result
  -> run_cycle            one cycle_id + poll timestamp for every ticker
  -> normalization        canonical rows, vendor fields preserved
  -> schemas              fixed PyArrow schemas, no pandas inference
  -> storage              read/merge/dedupe/validate/tmp-write/os.replace
  -> HealthTracker        daily health JSON, restored across restarts
  CollectorScheduler      absolute wall-clock boundaries, signal-aware
```

Separate processes: `watchdog.py` (silent-failure detection), `qa_report.py`
(range QA), `validate_dataset.py` (dataset properties), `backup.py`.

## Review findings (checked explicitly)

| Risk | Status |
| --- | --- |
| Timezone / DST | Session bounds are built as local clock time + zone, then converted to UTC; tested across both 2026 DST transitions. |
| Early close | Detected from XNYS; option end = equity close + configurable minutes; documented as an open question. |
| Scheduler drift | Boundaries are epoch-aligned and recomputed after each cycle; tested with 10s and 45s per-ticker work. |
| Overrun | Missed boundaries are skipped, never caught up in a burst. |
| Unbounded retries | Max 3 attempts, absurd `Retry-After` values ignored. |
| Unbounded memory | Only the current day is kept in memory (trackers and health both prune by date). |
| Token leakage | Token never logged, never in exceptions; response bodies scrubbed and truncated; test asserts it. |
| Parquet corruption | Temp file in the same directory, `os.replace`, existing file preserved on failure; tested. |
| Schema drift | Explicit schema on every write; incompatible files raise `SchemaDriftError` instead of being overwritten. |
| Duplicates | Dedupe on `(cycle_id, symbol)` / `(cycle_id, ticker)` keeping the newest. |
| Sticky loss after restart | Universe reloaded from per-day JSON; tested. |
| Partial Tradier responses | Failed batches are logged and skipped; remaining batches still persist. |
| Object/list/null shapes | Normalised in the client; tested for all three. |
| One-ticker failure | Contained in `TickerResult`; tested for one, several and all tickers failing. |
| Corrupt metadata | Quarantined as `.corrupt-<timestamp>`, never silently overwritten. |
| Restart behaviour | Sticky universe, health counters and observed timestamps all restored. |

## Known limitations

1. **Early-close option hours are an assumption, not a fact.**  Default is
   equity close + 15 minutes.  Verify against the Cboe holiday schedule before
   the first half day and set `EARLY_CLOSE_OPTION_EXTRA_MINUTES` accordingly.
2. **Read-modify-rewrite storage.**  Correct and atomic, but cost grows with
   file size through the day (tens of milliseconds per ticker per cycle at
   this volume).  At materially higher frequency, switch to partitioned
   append-only parts with end-of-day compaction, or DuckDB.  The change is
   isolated to `tradier_collector/storage.py`.
3. **Single process, sequential tickers.**  Three tickers at one minute is
   comfortable on an e2-micro; a much larger universe would need concurrency.
4. **No live-network tests.**  The suite mocks Tradier entirely by design; the
   real API is exercised only by `scripts/smoke_test_tradier.py`.
5. **`largest_gap_minutes` across a restart** relies on the observed
   timestamps stored in the health file; a health file deleted mid-day loses
   that history (coverage itself is recomputed by `qa_report.py` from the
   Parquet files, which remain authoritative).
6. **Spot fallback.**  When `last` is absent the bid/ask midpoint, then the
   previous close, is used.  Discovery is skipped entirely if none exist.
7. **Rate limits are not pre-emptively throttled**; the client reacts to 429s
   rather than pacing requests.  At 3 tickers x ~2 requests + 1 chain per
   minute this is well inside Tradier limits.

## Operational risks

* Token expiry or entitlement loss shows up as repeated 401s: the collector
  does not retry them, every ticker fails, and the watchdog goes fatal.
* Disk exhaustion would break atomic writes; the existing files survive, but
  monitor free space (30 GB is ample for years at this volume).
* A clock skew on the VM shifts poll boundaries; keep `systemd-timesyncd` on.
* If the health file is deleted while the collector runs, the watchdog reports
  fatal until the next periodic write (at most 5 cycles).

## Deployment checklist

- [ ] Non-root service account created; `.env` is `chmod 600` and not in git.
- [ ] `uv sync` completed; `.venv/bin/python` exists at the path in the units.
- [ ] `scripts/smoke_test_tradier.py` passes against the live API.
- [ ] `main.py --max-cycles 3` writes files under `data/`.
- [ ] Placeholders replaced in both unit files.
- [ ] `systemctl enable --now tradier-collector.service` and the watchdog timer.
- [ ] No inbound firewall rules added.
- [ ] VM timezone irrelevant, but NTP enabled.
- [ ] `EARLY_CLOSE_OPTION_EXTRA_MINUTES` decided before the next half day.

## First-week QA checklist

Day 1, after the close:

- [ ] `watchdog.py` exits 0.
- [ ] `qa_report.py --ticker SPY --start-date <day> --end-date <day>` shows
      COMPLETE for all three tickers (coverage >= 98%).
- [ ] `validate_dataset.py --ticker SPY --date <day>` passes every check,
      especially the two sticky-tracking checks.
- [ ] Contract counts look sane: ~42 at the open, growing as spot moves.
- [ ] Spot-check a morning contract far from the close spot: it must have
      observations right up to the last cycle.

Every day of week 1:

- [ ] Run the QA report over the week so far; investigate any PARTIAL or
      SUSPICIOUS day before it becomes a habit.
- [ ] Check `journalctl -u tradier-collector.service --since today` for
      restarts, batch failures and rate-limit warnings.
- [ ] Confirm the daily health JSON status is `healthy`.
- [ ] `backup.py --all-complete-days` and verify checksums exist.
- [ ] Watch file sizes and cycle elapsed time; elapsed should stay well under
      the 60-second interval.

## Production readiness statement

All 195 automated tests pass without network access, `ruff check` and
`mypy --strict` are clean, and the restart / data-integrity paths above were
reviewed and covered by tests.  The live API path has not been exercised in
this environment: run the smoke test and a bounded `--max-cycles` run on the
target VM before enabling the service.
