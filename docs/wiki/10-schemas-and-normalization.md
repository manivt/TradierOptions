# 10 - Schemas and normalization

[Index](README.md) | Prev: [09 - Collection cycle](09-collection-cycle.md) | Next: [11 - Storage](11-storage.md)

Code: `tradier_collector/schemas.py`, `tradier_collector/normalization.py`
Tests: `tests/test_normalization.py` (13)

## Why fixed schemas

Pandas infers dtypes from the data in hand. A day where `volume` happens to be
all null becomes `float64` or `object`, while the next day is `int64` - and a
multi-day load months later fails or silently coerces. Every write therefore
passes an explicit `pa.Schema`; nothing is inferred (I6).

Two schemas, both in `schemas.py`:

* `OPTION_SCHEMA` - 30 fields, key `(cycle_id, symbol)`.
* `UNDERLYING_SCHEMA` - 16 fields, key `(cycle_id, ticker)`.

Conventions: all timestamps `timestamp("us", tz="UTC")`; `expiration` is a
consistently encoded `YYYY-MM-DD` **string** (chosen over `date32` so every
consumer, including plain Parquet readers and the JSON metadata, sees the same
representation); prices `float64`; counts nullable `int64`.

Note: a nullable `int64` column materialises in pandas as `float64` when nulls
are present. The **file** keeps `int64` - that is what the invariant protects.
Use `types_mapper=pd.ArrowDtype` downstream if nullable integers matter.

## Coercion helpers

| Helper | Behaviour |
| --- | --- |
| `to_float` | `None`, empty string, bool, NaN, unparseable -> `None` |
| `to_int` | via `to_float`, then truncate (`"7.9"` -> `7`) |
| `epoch_ms_to_utc` | Tradier epoch ms -> aware UTC; `0` and negatives -> `None` |
| `parse_greeks_updated_at` | `YYYY-MM-DD HH:MM:SS` and ISO variants, read as **UTC** |
| `extract_spot` | last -> mid -> previous close -> `None` |

All of them are total: bad vendor data produces nulls and a warning, never an
exception, because one malformed field must not cost a whole cycle.

## Row builders

* `normalize_option_quote(...) -> dict | None` - returns `None` only when the
  payload has no usable `symbol` (the one field we cannot reconstruct). A
  missing `greeks` block is normal (Tradier omits it outside market hours) and
  yields nulls across all `tradier_*` fields.
* `normalize_underlying_quote(...) -> dict` - always succeeds; every field is
  independently nullable.

Field mapping notes: Tradier `bidsize`/`asksize` become `bid_size`/`ask_size`;
`prevclose` becomes `previous_close`; `option_type` is lower-cased.

## Vendor greeks policy (I5)

Stored verbatim as `tradier_delta`, `tradier_gamma`, `tradier_theta`,
`tradier_vega`, `tradier_rho`, `tradier_bid_iv`, `tradier_mid_iv`,
`tradier_ask_iv`, `tradier_smv_vol`, plus `tradier_greeks_updated_at`.

Never interpolated to minute resolution, never resampled, never replaced by
computed values. Future modelled greeks use `model_*` names
(`research/README.md`). `validate_dataset.py` asserts that vendor greek
timestamps are distinct from poll timestamps, so nobody can mistake one for the
other when reading the data cold.

## Explicitly absent

No VWAP, RSI, ATR, MACD, moving averages, spreads, mid prices, signals or
labels. Derived quantities belong downstream, where assumptions can change
without re-collecting (I19).

Related: [17 - Data dictionary](17-data-dictionary.md), [11 - Storage](11-storage.md)
