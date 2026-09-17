# 16 - Decision log

[Index](README.md) | Prev: [15 - Deployment and operations](15-deployment-operations.md) | Next: [17 - Data dictionary](17-data-dictionary.md)

Numbered decisions with the reasoning that produced them. Reverse any of these
only with the consequence column in view.

| # | Decision | Why | Consequence if reversed |
| --- | --- | --- | --- |
| D1 | Sticky intraday contract universe | A moving ATM window loses the exact rows needed to model an exit for a morning entry | Backtests cannot exit positions realistically; the dataset fails its stated purpose |
| D2 | `poll_timestamp_utc` comes from the scheduler, never Tradier | Vendor timestamps reflect their last update, not our sampling intent | Cycles stop aligning across tickers; coverage maths becomes meaningless |
| D3 | Vendor greeks kept under `tradier_*` with their own timestamp, never interpolated | Tradier greeks are not minute-resolution; faking that resolution contaminates volatility research | Silent, unrecoverable corruption of any IV or greek study |
| D4 | Fixed PyArrow schemas on every write | Pandas inference turns all-null int columns into floats, so daily files drift apart | Multi-month loads break or silently coerce |
| D5 | Explicit retry loop instead of `tenacity` | Needed `Retry-After` clamping and rate-limit header logging with a hard cap of 3 | Unbounded retries or unreadable decorator config, plus an unused dependency on a tiny VM |
| D6 | Absolute epoch-aligned poll boundaries | `collect(); sleep(60)` drifts by the processing time of every cycle | The sampling grid smears across the session |
| D7 | Overrun skips boundaries, never catches up | Catch-up bursts hit rate limits and produce clustered, misleading timestamps | 429 storms and samples attributed to the wrong minute |
| D8 | Temp file in the same directory plus `os.replace` | Atomicity holds only within one filesystem; a crash mid-write must not truncate a day | Corrupt Parquet files, silent data loss |
| D9 | Read-modify-rewrite rather than partitioned append | Simplest correct option at 1 cycle per minute; cost is tens of milliseconds | More moving parts than the workload justifies today |
| D10 | Corrupt metadata quarantined as `.corrupt-<ts>`, collection continues | Losing the rest of a session is worse than losing the morning universe, and the evidence is preserved | Either silent data loss or a dead ticker for the whole day |
| D11 | Local backup target only, behind a `BackupTarget` Protocol | The brief said not to implement GCS unless needed; the seam makes it a one-class change | Unnecessary cloud dependency and credentials on the VM |
| D12 | Watchdog as a separate process | A hung collector cannot report its own failure | Silent failures go unnoticed until QA runs days later |
| D13 | Expected cycles as an explicit timestamp list | Estimated counts hide holidays, half days and DST errors | Coverage percentages become unfalsifiable |
| D14 | Early-close rule = equity close plus configurable minutes (default 15) | Cboe's 2026 schedules differ by venue (13:00 ET for BZX/C2/EDGX, 13:15 ET for C1); `exchange_calendars` models equities only | Either data lost from the later venue or a hidden venue policy nobody can find |
| D15 | Underlying persisted before options | An options failure must not also cost the underlying series | One chain error loses two datasets instead of one |
| D16 | `TRADIER_API_KEY` accepted as an alias | The repo already had a `.env` using that name | A working deployment breaks on first run for no benefit |
| D17 | QA, watchdog and backup fall back to a synthetic token | Inspecting existing files must not require a live credential | Cannot run QA on an archive or on a machine without the token |
| D18 | Tickers collected sequentially | Three tickers take well under a second; concurrency adds failure modes for nothing | New race conditions and harder failure isolation |
| D19 | `ClockSettings` Protocol instead of importing `config` | Keeps the dependency graph one-way so QA tools import the clock freely | Import cycles, or QA tools dragging in collector config |
| D20 | Dependencies managed with `uv add` (pyproject plus uv.lock) | Owner's explicit instruction mid-build; reproducible resolution | Drifting environments between dev and the VM |
| D21 | `expiration` stored as a `YYYY-MM-DD` string | One representation across Parquet, JSON metadata and logs | Consumers must special-case date32 versus string per artefact |
| D22 | Health file rewritten every 5 cycles | A watchdog needs live progress, but not a write per cycle | Either a stale health view or needless IO |
| D23 | Oracle Linux uses an app-local uv Python under `.uv-python` | A default uv venv can resolve through `~/.local/share/uv`, which `ProtectHome=true` and Oracle SELinux may block | A systemd service that works interactively but fails with `203/EXEC` |
| D24 | Preserve vendor and future model Greeks side by side | Vendor values have their own freshness timestamp and model values depend on explicit assumptions | Research silently confuses a vendor field with a locally derived value |
| D25 | Defer off-VM backup automation until live collection is proven | A few completed days provide real inputs for testing idempotent transfer and restore validation | Premature automation with untested credentials or incomplete-day copies |

Related: [02 - Invariants](02-invariants.md), [19 - Limitations](19-limitations-and-future-work.md)
