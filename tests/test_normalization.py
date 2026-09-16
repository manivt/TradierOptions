from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa

from tests.conftest import POLL_TS, make_equity_quote, make_option_quote
from tradier_collector.normalization import (
    epoch_ms_to_utc,
    extract_spot,
    normalize_option_quote,
    normalize_underlying_quote,
    parse_greeks_updated_at,
    to_float,
    to_int,
)
from tradier_collector.schemas import OPTION_SCHEMA, UNDERLYING_SCHEMA
from tradier_collector.storage import rows_to_table


def normalize(raw: dict[str, object]) -> dict[str, object]:
    row = normalize_option_quote(
        raw,
        cycle_id="cyc-1",
        poll_timestamp_utc=POLL_TS,
        request_started_utc=POLL_TS,
        request_completed_utc=POLL_TS,
        ticker="SPY",
        expiration="2026-09-15",
        underlying_spot=600.25,
    )
    assert row is not None
    return row


def test_complete_option_record() -> None:
    row = normalize(make_option_quote())
    assert row["symbol"] == "SPY260915C00600000"
    assert row["bid"] == 1.20
    assert row["bid_size"] == 40
    assert row["option_type"] == "call"
    assert row["tradier_delta"] == 0.51
    assert row["tradier_smv_vol"] == 0.125
    assert row["underlying_spot"] == 600.25
    assert row["poll_timestamp_utc"] == POLL_TS
    assert row["tradier_greeks_updated_at"] == datetime(2026, 9, 15, 13, 25, 4, tzinfo=UTC)
    # The vendor greek timestamp must never equal the poll timestamp by accident.
    assert row["tradier_greeks_updated_at"] != row["poll_timestamp_utc"]


def test_missing_bid_ask_becomes_null() -> None:
    row = normalize(make_option_quote(bid=None, ask=None, bidsize=None, asksize=None))
    assert row["bid"] is None
    assert row["ask"] is None
    assert row["bid_size"] is None


def test_missing_greeks_block() -> None:
    quote = make_option_quote()
    del quote["greeks"]
    row = normalize(quote)
    assert row["tradier_delta"] is None
    assert row["tradier_greeks_updated_at"] is None


def test_partial_greeks_block() -> None:
    row = normalize(make_option_quote(greeks={"delta": 0.3, "updated_at": None}))
    assert row["tradier_delta"] == 0.3
    assert row["tradier_vega"] is None
    assert row["tradier_greeks_updated_at"] is None


def test_null_volume_and_open_interest() -> None:
    row = normalize(make_option_quote(volume=None, open_interest=None))
    assert row["volume"] is None
    assert row["open_interest"] is None


def test_string_numerics_are_coerced() -> None:
    row = normalize(make_option_quote(bid="1.05", volume="42") | {"strike": "600"})
    assert row["bid"] == 1.05
    assert row["volume"] == 42
    assert row["strike"] == 600.0


def test_quote_without_symbol_is_dropped() -> None:
    assert (
        normalize_option_quote(
            {"strike": 600.0},
            cycle_id="c",
            poll_timestamp_utc=POLL_TS,
            request_started_utc=None,
            request_completed_utc=None,
            ticker="SPY",
            expiration="2026-09-15",
            underlying_spot=None,
        )
        is None
    )


def test_epoch_conversion() -> None:
    assert epoch_ms_to_utc(1789500000000) == datetime.fromtimestamp(1789500000, tz=UTC)
    assert epoch_ms_to_utc(0) is None
    assert epoch_ms_to_utc(None) is None
    assert epoch_ms_to_utc("not-a-number") is None


def test_greeks_timestamp_formats() -> None:
    assert parse_greeks_updated_at("2026-09-15 13:25:04") == datetime(
        2026, 9, 15, 13, 25, 4, tzinfo=UTC
    )
    assert parse_greeks_updated_at("2026-09-15T13:25:04") == datetime(
        2026, 9, 15, 13, 25, 4, tzinfo=UTC
    )
    assert parse_greeks_updated_at("garbage") is None
    assert parse_greeks_updated_at(None) is None


def test_scalar_coercion_helpers() -> None:
    assert to_float(True) is None
    assert to_float(float("nan")) is None
    assert to_float("") is None
    assert to_int("7.9") == 7
    assert to_int(None) is None


def test_underlying_record() -> None:
    row = normalize_underlying_quote(
        make_equity_quote(),
        cycle_id="cyc-1",
        poll_timestamp_utc=POLL_TS,
        request_started_utc=POLL_TS,
        request_completed_utc=POLL_TS,
        ticker="SPY",
    )
    assert row["ticker"] == "SPY"
    assert row["previous_close"] == 599.75
    assert row["high"] == 601.75
    assert set(row) == set(UNDERLYING_SCHEMA.names)


def test_spot_extraction_fallbacks() -> None:
    assert extract_spot(make_equity_quote(last=600.0)) == 600.0
    assert extract_spot(make_equity_quote(last=None, bid=10.0, ask=11.0)) == 10.5
    assert extract_spot({"last": None, "bid": None, "ask": None, "prevclose": 42.0}) == 42.0
    assert extract_spot({}) is None


def test_dtypes_survive_all_null_columns() -> None:
    quote = make_option_quote(volume=None, open_interest=None, bid=None, bidsize=None)
    del quote["greeks"]
    table = rows_to_table([normalize(quote)], OPTION_SCHEMA)
    assert table.schema.equals(OPTION_SCHEMA)
    assert table.schema.field("volume").type == pa.int64()
    assert table.schema.field("tradier_delta").type == pa.float64()
