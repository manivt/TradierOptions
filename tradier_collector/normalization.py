"""Canonical normalisation of raw Tradier JSON into schema-shaped rows.

Assumptions about Tradier response fields (documented in README.md):

* ``trade_date`` / ``bid_date`` / ``ask_date`` are epoch **milliseconds**.
  Values of 0 mean "never traded / no quote yet" and are stored as null.
* ``greeks.updated_at`` is a naive datetime string such as
  ``2026-09-15 15:35:04`` and is interpreted as **UTC**.  It is preserved as
  the vendor greek timestamp and is deliberately never used as the collector
  poll timestamp.
* Numeric fields occasionally arrive as strings; they are coerced defensively.
* Missing keys are normal (Tradier omits greeks outside market hours) and must
  produce nulls, never exceptions and never dtype drift.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

GREEK_FIELDS = {
    "tradier_delta": "delta",
    "tradier_gamma": "gamma",
    "tradier_theta": "theta",
    "tradier_vega": "vega",
    "tradier_rho": "rho",
    "tradier_bid_iv": "bid_iv",
    "tradier_mid_iv": "mid_iv",
    "tradier_ask_iv": "ask_iv",
    "tradier_smv_vol": "smv_vol",
}

_GREEK_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f")


def to_float(value: Any) -> float | None:
    """Coerce to float, returning None for missing or unparseable values."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def to_int(value: Any) -> int | None:
    """Coerce to int, tolerating floats and numeric strings."""
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def epoch_ms_to_utc(value: Any) -> datetime | None:
    """Convert Tradier epoch milliseconds into an aware UTC datetime."""
    number = to_float(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        logger.warning("Unparseable epoch-millisecond timestamp: %r", value)
        return None


def parse_greeks_updated_at(value: Any) -> datetime | None:
    """Parse the vendor greek timestamp, assumed to be UTC."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return epoch_ms_to_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "")
    for fmt in _GREEK_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC)
    logger.warning("Unparseable greeks.updated_at value: %r", value)
    return None


def normalize_option_quote(
    raw: dict[str, Any],
    *,
    cycle_id: str,
    poll_timestamp_utc: datetime,
    request_started_utc: datetime | None,
    request_completed_utc: datetime | None,
    ticker: str,
    expiration: str,
    underlying_spot: float | None,
) -> dict[str, Any] | None:
    """Turn one raw Tradier option quote into a canonical option row.

    Returns None when the payload has no usable symbol, which is the only
    field we cannot reconstruct or null out.
    """
    symbol = raw.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        logger.warning("Skipping option quote without a symbol: keys=%s", sorted(raw))
        return None

    greeks_raw = raw.get("greeks")
    greeks: dict[str, Any] = greeks_raw if isinstance(greeks_raw, dict) else {}

    option_type = raw.get("option_type")
    row: dict[str, Any] = {
        "cycle_id": cycle_id,
        "poll_timestamp_utc": poll_timestamp_utc,
        "request_started_utc": request_started_utc,
        "request_completed_utc": request_completed_utc,
        "ticker": ticker,
        "expiration": expiration,
        "underlying_spot": to_float(underlying_spot),
        "symbol": symbol.strip(),
        "strike": to_float(raw.get("strike")),
        "option_type": str(option_type).lower() if isinstance(option_type, str) else None,
        "bid": to_float(raw.get("bid")),
        "bid_size": to_int(raw.get("bidsize")),
        "ask": to_float(raw.get("ask")),
        "ask_size": to_int(raw.get("asksize")),
        "last": to_float(raw.get("last")),
        "volume": to_int(raw.get("volume")),
        "open_interest": to_int(raw.get("open_interest")),
        "trade_date": epoch_ms_to_utc(raw.get("trade_date")),
        "bid_date": epoch_ms_to_utc(raw.get("bid_date")),
        "ask_date": epoch_ms_to_utc(raw.get("ask_date")),
        "tradier_greeks_updated_at": parse_greeks_updated_at(greeks.get("updated_at")),
    }
    for column, source in GREEK_FIELDS.items():
        row[column] = to_float(greeks.get(source))
    return row


def normalize_underlying_quote(
    raw: dict[str, Any],
    *,
    cycle_id: str,
    poll_timestamp_utc: datetime,
    request_started_utc: datetime | None,
    request_completed_utc: datetime | None,
    ticker: str,
) -> dict[str, Any]:
    """Turn one raw Tradier equity quote into a canonical underlying row."""
    return {
        "cycle_id": cycle_id,
        "poll_timestamp_utc": poll_timestamp_utc,
        "request_started_utc": request_started_utc,
        "request_completed_utc": request_completed_utc,
        "ticker": ticker,
        "bid": to_float(raw.get("bid")),
        "ask": to_float(raw.get("ask")),
        "last": to_float(raw.get("last")),
        "volume": to_int(raw.get("volume")),
        "open": to_float(raw.get("open")),
        "high": to_float(raw.get("high")),
        "low": to_float(raw.get("low")),
        "previous_close": to_float(raw.get("prevclose")),
        "trade_date": epoch_ms_to_utc(raw.get("trade_date")),
        "bid_date": epoch_ms_to_utc(raw.get("bid_date")),
        "ask_date": epoch_ms_to_utc(raw.get("ask_date")),
    }


def extract_spot(raw: dict[str, Any]) -> float | None:
    """Best available spot price for an equity quote.

    Preference order: last trade, then the bid/ask midpoint, then the previous
    close.  Returning None is legitimate (pre-market, halted symbol) and the
    caller decides what to do about it.
    """
    last = to_float(raw.get("last"))
    if last is not None and last > 0:
        return last
    bid = to_float(raw.get("bid"))
    ask = to_float(raw.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    prev_close = to_float(raw.get("prevclose"))
    if prev_close is not None and prev_close > 0:
        return prev_close
    return None
