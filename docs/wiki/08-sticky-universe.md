# 08 - Sticky universe

[Index](README.md) | Prev: [07 - Expiration and discovery](07-expiration-and-discovery.md) | Next: [09 - Collection cycle](09-collection-cycle.md)

Code: `tradier_collector/contract_tracker.py` | Tests: `tests/test_contract_tracker.py` (11)

**This is the defining design decision of the project.  Read this before
changing anything in the collection path.**

## The problem it solves

A naive collector samples the current ATM +/- 10 window every minute.  The
window moves with spot, so a contract that was ATM at 09:40 silently disappears
from the dataset once the underlying drifts.  Any backtest that opens a position
in the morning then has **no quotes to exit against** - the exact rows a
realistic entry/exit and slippage study needs are the ones such a design throws
away.

## The rule

> Once a contract enters the discovery window on a trading day, it is quoted for
> the remainder of that session.  Contracts are never removed intraday.

The universe therefore grows monotonically through the day.  Example: SPY opens
near 600 and 590-610 is discovered; by 11:00 spot is 607 and 611-617 join; the
590-596 strikes stay tracked and keep producing rows even though they are far
out of the money.

## Persistence and restart safety

State lives at `data/<TICKER>/metadata/YYYY-MM-DD_contracts.json`:

```json
{
  "schema_version": 1,
  "ticker": "SPY",
  "trading_date": "2026-09-15",
  "updated_utc": "2026-09-15T15:12:03.114+00:00",
  "contract_count": 58,
  "contracts": [
    {"symbol": "SPY260915C00600000", "ticker": "SPY", "expiration": "2026-09-15",
     "strike": 600.0, "option_type": "call",
     "first_discovered_utc": "2026-09-15T13:30:00+00:00"}
  ]
}
```

* Written atomically (temp file plus `os.replace`) after every change.
* `first_discovered_utc` is the cycle poll timestamp, not wall-clock-at-write,
  so discovery times align with the option rows.
* Duplicates keep their **original** discovery time - re-discovery never resets it.
* A restart at noon calls `load()` and resumes with the full morning universe (I2).
* The file is per day, so a new trading date starts empty by construction.

## Corrupt metadata

`_handle_corrupt` never overwrites a damaged file.  It renames it to
`<name>.corrupt-<UTC timestamp>`, logs at ERROR, and starts a fresh universe so
collection continues.  With `strict=True` it raises `CorruptMetadataError`
instead (used by tooling that would rather stop than proceed).

Treated as corruption: unparseable JSON, a non-object root, a missing/non-list
`contracts` field, and a `trading_date` that does not match the expected day
(mixing two sessions into one universe would be worse than starting over).
Individual unusable records are skipped and counted, not fatal.

## Cost characteristics

Universe size drives request volume: 42 contracts at the open, typically under
150 by the close on a trending day.  At `QUOTE_BATCH_SIZE=50` that is 1-3 batch
requests per ticker per minute.  The universe only grows, so the last cycle of
the day is the most expensive one - see [11 - Storage](11-storage.md) for the
matching write-cost note.

## How to prove it works on real data

`uv run python validate_dataset.py --ticker SPY --date <day>` checks that
morning-discovered contracts still report in the final cycles, and that
contracts far from the closing spot are still being collected in the last 30
minutes - which can only happen if tracking is sticky.

Related: [02 - Invariants](02-invariants.md) I1/I2/I15,
[13 - Validation and backup](13-validation-and-backup.md)
