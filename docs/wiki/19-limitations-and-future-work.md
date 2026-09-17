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
2. **Live API path unverified in this environment.** The whole test suite is
   offline by design; `scripts/smoke_test_tradier.py` and a bounded
   `main.py --max-cycles 3` have not been run against the real API from this
   machine. Do both on the VM before enabling the service.

## Known limitations

| Area | Limitation | Mitigation or trigger to fix |
| --- | --- | --- |
| Storage | Read-modify-rewrite cost grows with file size | Fine at 1/min; switch to partitioned append-only or DuckDB at higher frequency |
| Concurrency | Tickers are collected sequentially in one process | Fine for 3 tickers; revisit beyond roughly 10 |
| Rate limits | Reactive (handles 429) rather than pre-emptive pacing | Add a token bucket if the universe or ticker list grows |
| Gap history | `largest_gap_minutes` across a restart relies on the health file | Parquet stays authoritative; `qa_report.py` recomputes from data |
| Spot | Falls back to mid, then previous close; discovery skipped if none | Acceptable: a stale spot would pick the wrong ATM window |
| Test coverage | 92%; argparse wiring of the `backup.py` and `watchdog.py` mains untested | Their logic is tested via `backup_day` and `check_day` |
| Greeks | Vendor only; no model greeks | Deliberate - see `research/README.md` |
| Backups | Local directory only | `BackupTarget` Protocol makes GCS a one-class addition |
| Metadata growth | Universe JSON rewritten on every discovery change | Small (hundreds of records); only an issue with far larger windows |

## Deliberately not implemented

Trading, order placement, position tracking, strategy logic, machine-learning
models, indicators (VWAP, RSI, ATR, MACD, moving averages), model-derived
greeks, a web server or any inbound network surface, and cloud storage.

## Natural next steps

1. Reconfirm the early-close venue policy when Tradier changes its feed or an
   exchange changes its schedule; record any change in
   [16 - Decision log](16-decision-log.md).
2. Run the first-day and first-week QA procedures in `RELEASE_REVIEW.md`.
3. Once weeks of data exist, build the feature-engineering layer described in
   `research/README.md` as a **separate** package that reads `data/` - the
   collector should not grow research code.
4. If durability becomes a concern, implement a GCS `BackupTarget`.
5. If the universe grows substantially, revisit `DISCOVERY_INTERVAL_CYCLES`,
   batch size and the storage format together.

Related: [16 - Decision log](16-decision-log.md), [02 - Invariants](02-invariants.md)
