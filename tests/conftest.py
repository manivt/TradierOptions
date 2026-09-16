"""Shared fixtures.  No test in this suite touches the network."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, load_settings  # noqa: E402

TRADING_DAY = date(2026, 9, 15)  # a Tuesday
POLL_TS = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        {
            "TRADIER_API_TOKEN": "test-token",
            "TICKERS": "SPY,QQQ",
            "DATA_DIR": str(tmp_path / "data"),
            "LOG_DIR": str(tmp_path / "logs"),
        },
        env_file=None,
    )


def make_option_quote(
    symbol: str = "SPY260915C00600000",
    strike: float = 600.0,
    option_type: str = "call",
    **overrides: Any,
) -> dict[str, Any]:
    quote: dict[str, Any] = {
        "symbol": symbol,
        "description": f"SPY Sep 15 2026 ${strike} Call",
        "type": "option",
        "last": 1.23,
        "volume": 4567,
        "bid": 1.20,
        "ask": 1.26,
        "bidsize": 40,
        "asksize": 25,
        "open_interest": 1234,
        "strike": strike,
        "option_type": option_type,
        "expiration_date": "2026-09-15",
        "trade_date": 1789500000000,
        "bid_date": 1789500060000,
        "ask_date": 1789500060000,
        "greeks": {
            "delta": 0.51,
            "gamma": 0.09,
            "theta": -0.85,
            "vega": 0.04,
            "rho": 0.0012,
            "bid_iv": 0.11,
            "mid_iv": 0.12,
            "ask_iv": 0.13,
            "smv_vol": 0.125,
            "updated_at": "2026-09-15 13:25:04",
        },
    }
    quote.update(overrides)
    return quote


def make_equity_quote(
    symbol: str = "SPY", last: float | None = 600.25, **overrides: Any
) -> dict[str, Any]:
    reference = last if last is not None else 0.0
    quote: dict[str, Any] = {
        "symbol": symbol,
        "description": symbol,
        "type": "etf",
        "last": last,
        "bid": reference - 0.01,
        "ask": reference + 0.01,
        "volume": 12_345_678,
        "open": reference - 1.0,
        "high": reference + 1.5,
        "low": reference - 2.0,
        "prevclose": reference - 0.5,
        "trade_date": 1789500000000,
        "bid_date": 1789500060000,
        "ask_date": 1789500060000,
    }
    quote.update(overrides)
    return quote


def make_chain(
    center: float = 600.0,
    strikes_each_side: int = 15,
    step: float = 1.0,
    expiration: str = "2026-09-15",
) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    for index in range(-strikes_each_side, strikes_each_side + 1):
        strike = center + index * step
        for option_type, code in (("call", "C"), ("put", "P")):
            symbol = f"SPY260915{code}{int(strike * 1000):08d}"
            chain.append(
                make_option_quote(symbol=symbol, strike=strike, option_type=option_type)
                | {"expiration_date": expiration}
            )
    return chain
