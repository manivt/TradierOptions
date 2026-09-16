"""Atomic daily Parquet storage.

Write strategy: read existing -> concatenate -> deduplicate -> validate against
the canonical schema -> write a temporary file in the *same directory* ->
``os.replace``.  Because the temp file shares a filesystem with the target, the
replace is atomic, so a crash mid-write can never leave a truncated Parquet
file behind, and the previous good file survives any failure.

At one cycle per minute this read-modify-rewrite costs a few tens of
milliseconds per ticker per cycle, which is entirely acceptable.  At a
substantially higher collection frequency (say, sub-second sampling or many
dozens of underlyings) this design should be replaced by an append-only,
partitioned layout (hourly Parquet parts compacted at end of day) or an
embedded database such as DuckDB; that change is isolated to this module.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import (
    OPTION_KEY,
    OPTION_SCHEMA,
    UNDERLYING_KEY,
    UNDERLYING_SCHEMA,
)

logger = logging.getLogger(__name__)

COMPRESSION = "snappy"
TEMP_SUFFIX = ".tmp-"
#: Temp files older than this are considered abandoned by a dead process.
STALE_TEMP_SECONDS = 3600


class SchemaDriftError(RuntimeError):
    """Raised when an existing file cannot be reconciled with the schema."""


# ----------------------------------------------------------------- layout


def ticker_dir(data_dir: Path | str, ticker: str) -> Path:
    return Path(data_dir) / ticker


def option_path(data_dir: Path | str, ticker: str, day: date) -> Path:
    return ticker_dir(data_dir, ticker) / "options" / f"{day.isoformat()}.parquet"


def underlying_path(data_dir: Path | str, ticker: str, day: date) -> Path:
    return ticker_dir(data_dir, ticker) / "underlying" / f"{day.isoformat()}.parquet"


def metadata_path(data_dir: Path | str, ticker: str, day: date) -> Path:
    return ticker_dir(data_dir, ticker) / "metadata" / f"{day.isoformat()}_contracts.json"


def health_path(data_dir: Path | str, day: date) -> Path:
    return Path(data_dir) / "health" / f"{day.isoformat()}.json"


def ensure_layout(data_dir: Path | str, tickers: list[str] | tuple[str, ...]) -> None:
    """Create the directory tree up front so writes never race on mkdir."""
    root = Path(data_dir)
    (root / "health").mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        for sub in ("options", "underlying", "metadata"):
            (root / ticker / sub).mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------- primitives


def rows_to_table(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Build a table using the canonical schema explicitly.

    Missing keys become nulls of the declared type; extra keys are dropped.
    Nothing is inferred, so an all-null int64 column stays int64.
    """
    shaped = [{name: row.get(name) for name in schema.names} for row in rows]
    return pa.Table.from_pylist(shaped, schema=schema)


def _conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Cast an existing table to the canonical schema, or fail loudly."""
    if table.schema.equals(schema):
        return table
    missing = [n for n in schema.names if n not in table.schema.names]
    extra = [n for n in table.schema.names if n not in schema.names]
    if missing or extra:
        raise SchemaDriftError(
            f"Existing file does not match the canonical schema "
            f"(missing={missing}, unexpected={extra})"
        )
    try:
        return table.select(list(schema.names)).cast(schema)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        raise SchemaDriftError(f"Existing file cannot be cast to the schema: {exc}") from exc


def _dedupe_keep_last(table: pa.Table, keys: tuple[str, ...]) -> pa.Table:
    """Keep the last row for each key tuple (newest observation wins)."""
    if table.num_rows == 0:
        return table
    columns = [table.column(key).to_pylist() for key in keys]
    last_index: dict[tuple[Any, ...], int] = {}
    for index, key in enumerate(zip(*columns, strict=True)):
        last_index[key] = index
    if len(last_index) == table.num_rows:
        return table
    keep = sorted(last_index.values())
    return table.take(pa.array(keep, type=pa.int64()))


def clean_stale_temp_files(directory: Path, *, max_age_seconds: int = STALE_TEMP_SECONDS) -> int:
    """Remove obviously abandoned temp files.  Conservative by design.

    Only files created by this module are considered, and only once they are
    older than ``max_age_seconds``, so a concurrent writer is never disturbed.
    """
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for candidate in directory.glob(f"*{TEMP_SUFFIX}*"):
        try:
            if now - candidate.stat().st_mtime < max_age_seconds:
                continue
            candidate.unlink()
            removed += 1
            logger.warning("Removed stale temp file %s", candidate)
        except OSError as exc:
            logger.warning("Could not remove stale temp file %s: %s", candidate, exc)
    return removed


def _atomic_write_table(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}{TEMP_SUFFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        pq.write_table(table, tmp, compression=COMPRESSION)
        os.replace(tmp, path)
    except BaseException:
        # Leave the existing production file untouched.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            logger.warning("Failed to remove temp file %s after a write error", tmp)
        raise


def _append_snapshot(
    rows: list[dict[str, Any]],
    path: Path,
    schema: pa.Schema,
    keys: tuple[str, ...],
    sort_columns: list[tuple[str, str]],
) -> int:
    """Merge ``rows`` into the daily file atomically; returns rows written."""
    if not rows:
        return 0
    new_table = rows_to_table(rows, schema)
    clean_stale_temp_files(path.parent)

    if path.exists():
        existing = _conform(pq.read_table(path), schema)
        combined = pa.concat_tables([existing, new_table])
    else:
        combined = new_table

    combined = _dedupe_keep_last(combined, keys)
    combined = combined.sort_by(sort_columns)
    combined = _conform(combined, schema)
    _atomic_write_table(combined, path)
    return int(new_table.num_rows)


def append_option_snapshot(
    rows: list[dict[str, Any]],
    *,
    data_dir: Path | str,
    ticker: str,
    day: date,
) -> int:
    """Append option rows for one cycle.  Deduped on (cycle_id, symbol)."""
    return _append_snapshot(
        rows,
        option_path(data_dir, ticker, day),
        OPTION_SCHEMA,
        OPTION_KEY,
        [("poll_timestamp_utc", "ascending"), ("symbol", "ascending")],
    )


def append_underlying_snapshot(
    rows: list[dict[str, Any]],
    *,
    data_dir: Path | str,
    ticker: str,
    day: date,
) -> int:
    """Append underlying rows for one cycle.  Deduped on (cycle_id, ticker)."""
    return _append_snapshot(
        rows,
        underlying_path(data_dir, ticker, day),
        UNDERLYING_SCHEMA,
        UNDERLYING_KEY,
        [("poll_timestamp_utc", "ascending"), ("ticker", "ascending")],
    )


def load_option_day(data_dir: Path | str, ticker: str, day: date) -> pd.DataFrame:
    """Load one day of option observations (empty frame when absent)."""
    return _load(option_path(data_dir, ticker, day), OPTION_SCHEMA)


def load_underlying_day(data_dir: Path | str, ticker: str, day: date) -> pd.DataFrame:
    """Load one day of underlying observations (empty frame when absent)."""
    return _load(underlying_path(data_dir, ticker, day), UNDERLYING_SCHEMA)


def _load(path: Path, schema: pa.Schema) -> pd.DataFrame:
    if not path.exists():
        return cast(pd.DataFrame, schema.empty_table().to_pandas())
    table = _conform(pq.read_table(path), schema)
    return cast(pd.DataFrame, table.to_pandas())
