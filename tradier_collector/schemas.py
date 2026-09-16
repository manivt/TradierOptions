"""Fixed PyArrow schemas for the persisted datasets.

Every write goes through these schemas explicitly.  We never let pandas infer
dtypes for persisted files: inference silently turns an all-null int64 column
into a float64 (or object) column, which produces schema drift across daily
files and breaks multi-day loads months later.

Conventions
-----------
* All timestamps are stored as UTC microsecond timestamps.
* ``expiration`` is a consistently encoded ``YYYY-MM-DD`` string.
* Vendor greeks keep the ``tradier_`` prefix.  Locally modelled greeks, when
  they are eventually added, must use ``model_`` names so the two can never be
  confused or silently overwritten.
"""

from __future__ import annotations

import pyarrow as pa

UTC_TS = pa.timestamp("us", tz="UTC")

OPTION_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("cycle_id", pa.string(), nullable=False),
        pa.field("poll_timestamp_utc", UTC_TS, nullable=False),
        pa.field("request_started_utc", UTC_TS, nullable=True),
        pa.field("request_completed_utc", UTC_TS, nullable=True),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("expiration", pa.string(), nullable=False),
        pa.field("underlying_spot", pa.float64(), nullable=True),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("strike", pa.float64(), nullable=True),
        pa.field("option_type", pa.string(), nullable=True),
        pa.field("bid", pa.float64(), nullable=True),
        pa.field("bid_size", pa.int64(), nullable=True),
        pa.field("ask", pa.float64(), nullable=True),
        pa.field("ask_size", pa.int64(), nullable=True),
        pa.field("last", pa.float64(), nullable=True),
        pa.field("volume", pa.int64(), nullable=True),
        pa.field("open_interest", pa.int64(), nullable=True),
        pa.field("trade_date", UTC_TS, nullable=True),
        pa.field("bid_date", UTC_TS, nullable=True),
        pa.field("ask_date", UTC_TS, nullable=True),
        pa.field("tradier_delta", pa.float64(), nullable=True),
        pa.field("tradier_gamma", pa.float64(), nullable=True),
        pa.field("tradier_theta", pa.float64(), nullable=True),
        pa.field("tradier_vega", pa.float64(), nullable=True),
        pa.field("tradier_rho", pa.float64(), nullable=True),
        pa.field("tradier_bid_iv", pa.float64(), nullable=True),
        pa.field("tradier_mid_iv", pa.float64(), nullable=True),
        pa.field("tradier_ask_iv", pa.float64(), nullable=True),
        pa.field("tradier_smv_vol", pa.float64(), nullable=True),
        pa.field("tradier_greeks_updated_at", UTC_TS, nullable=True),
    ]
)

UNDERLYING_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("cycle_id", pa.string(), nullable=False),
        pa.field("poll_timestamp_utc", UTC_TS, nullable=False),
        pa.field("request_started_utc", UTC_TS, nullable=True),
        pa.field("request_completed_utc", UTC_TS, nullable=True),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("bid", pa.float64(), nullable=True),
        pa.field("ask", pa.float64(), nullable=True),
        pa.field("last", pa.float64(), nullable=True),
        pa.field("volume", pa.int64(), nullable=True),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("previous_close", pa.float64(), nullable=True),
        pa.field("trade_date", UTC_TS, nullable=True),
        pa.field("bid_date", UTC_TS, nullable=True),
        pa.field("ask_date", UTC_TS, nullable=True),
    ]
)

OPTION_COLUMNS: tuple[str, ...] = tuple(OPTION_SCHEMA.names)
UNDERLYING_COLUMNS: tuple[str, ...] = tuple(UNDERLYING_SCHEMA.names)

#: Dedupe keys for the two datasets.
OPTION_KEY: tuple[str, ...] = ("cycle_id", "symbol")
UNDERLYING_KEY: tuple[str, ...] = ("cycle_id", "ticker")
