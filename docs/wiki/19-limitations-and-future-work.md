# 19 - Limitations and future work

[Index](README.md) | Prev: [18 - Failure modes](18-failure-modes.md) | Next: [20 - Build history](20-build-history.md)

Honest boundaries of the current implementation. Also summarised in
`RELEASE_REVIEW.md`.

## Open questions

1. **Early-close venue policy.** Cboe's 2026 schedules show a 13:00 ET close
   for BZX/C2/EDGX and 13:15 ET for C1. The rule is equity close plus
   `EARLY_CLOSE_OPTION_EXTRA_MINUTES` (default 15), preserving the later
   consolidated-quote window. Revisit it if Tradier changes feed venues. See
   [05 - Market clock](05-market-clock.md).
2. **Opening-boundary wake-up race.** First live-day collection produced 405
   rather than 406 samples because the OS resumed just after 09:30 ET. A
   five-second tolerance plus stale-cycle guard is prepared and targeted tests
   pass, but the revision must still be deployed to the VM.

## Known limitations

| Area | Limitation | Mitigation or trigger to fix |
| --- | --- | --- |
| Storage | Read-modify-rewrite cost grows with file size | Fine at 1/min; switch to partitioned append-only or DuckDB at higher frequency |
| Concurrency | Tickers are collected sequentially in one process | Fine for 3 tickers; revisit beyond roughly 10 |
| Rate limits | Reactive (handles 429) rather than pre-emptive pacing | Add a token bucket if the universe or ticker list grows |
| Gap history | `largest_gap_minutes` across a restart relies on the health file | Parquet stays authoritative; `qa_report.py` recomputes from data |
| Spot | Falls back to mid, then previous close; discovery skipped if none | Acceptable: a stale spot would pick the wrong ATM window |
| Test coverage | 92%; argparse wiring of the `backup.py` and `watchdog.py` mains untested | Their logic is tested via `backup_day` and `check_day` |
| Greeks | Vendor only; vendor timestamps can be older than a poll | Offline `model_*` enrichment may use py_vollib after time-to-expiry, rate and dividend conventions are specified |
| Backups | Local directory only; no timer and no off-VM copy | After several successful days, implement a checksummed, catch-up-safe pull to the owner's Linux desktop; OCI Object Storage remains optional |
| Metadata growth | Universe JSON rewritten on every discovery change | Small (hundreds of records); only an issue with far larger windows |

## Deliberately not implemented

Trading, order placement, position tracking, strategy logic, machine-learning
models, indicators (VWAP, RSI, ATR, MACD, moving averages), model-derived
greeks, a web server or any inbound network surface, and cloud storage.

## Natural next steps

1. Reconfirm the early-close venue policy when Tradier changes its feed or an
   exchange changes its schedule; record any change in
   [16 - Decision log](16-decision-log.md).
2. Allow several more trading days to complete, then implement and test the
   off-VM desktop pull/restore workflow described in [13 - Validation and
   backup](13-validation-and-backup.md).
3. Deploy the tested opening-boundary scheduler revision before the next
   session where 406/406 coverage is required.
4. Once weeks of data exist, build the feature-engineering layer described in
   `research/README.md` as a **separate** package that reads `data/` - the
   collector should not grow research code.
5. Compare offline Black-Scholes-Merton `model_*` Greeks with stored
   `tradier_*` values before using them for research.
6. If durability becomes a concern beyond the desktop copy, implement an OCI
   Object Storage `BackupTarget` and regularly test restores.
7. If the universe grows substantially, revisit `DISCOVERY_INTERVAL_CYCLES`,
   batch size and the storage format together.

Related: [16 - Decision log](16-decision-log.md), [02 - Invariants](02-invariants.md)
