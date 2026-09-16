from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import make_chain
from tradier_collector.strike_selector import find_atm_strike, select_atm_window


def test_exact_atm_match() -> None:
    window = select_atm_window(make_chain(center=600.0), spot=600.0, strikes_each_side=10)
    assert window.atm_strike == 600.0
    assert len(window.strikes) == 21
    assert len(window.contracts) == 42
    assert window.strikes[0] == 590.0
    assert window.strikes[-1] == 610.0


def test_spot_between_strikes_picks_nearest() -> None:
    window = select_atm_window(make_chain(center=600.0), spot=603.4, strikes_each_side=2)
    assert window.atm_strike == 603.0
    assert window.strikes == [601.0, 602.0, 603.0, 604.0, 605.0]


def test_exact_midpoint_tie_picks_the_lower_strike() -> None:
    assert find_atm_strike([599.0, 600.0], 599.5) == 599.0
    window = select_atm_window(make_chain(center=600.0), spot=600.5, strikes_each_side=1)
    assert window.atm_strike == 600.0


def test_truncated_lower_edge() -> None:
    chain = make_chain(center=600.0, strikes_each_side=3)  # 597..603
    window = select_atm_window(chain, spot=597.0, strikes_each_side=10)
    assert window.atm_strike == 597.0
    assert window.truncated_low is True
    assert window.truncated_high is True
    assert window.strikes == [597.0, 598.0, 599.0, 600.0, 601.0, 602.0, 603.0]


def test_truncated_upper_edge_only() -> None:
    chain = make_chain(center=600.0, strikes_each_side=12)  # 588..612
    window = select_atm_window(chain, spot=610.0, strikes_each_side=5)
    assert window.truncated_high is True
    assert window.truncated_low is False
    assert window.strikes[-1] == 612.0


def test_missing_put_side_is_tolerated() -> None:
    chain = [c for c in make_chain(center=600.0, strikes_each_side=2) if not (
        c["strike"] == 600.0 and c["option_type"] == "put"
    )]
    window = select_atm_window(chain, spot=600.0, strikes_each_side=2)
    assert window.atm_strike == 600.0
    assert len(window.contracts) == 9
    assert "SPY260915P00600000" not in window.symbols


def test_invalid_entries_are_ignored() -> None:
    chain: list[dict[str, Any]] = [
        {"symbol": "OK", "strike": 600.0, "option_type": "call"},
        {"symbol": "", "strike": 600.0, "option_type": "put"},
        {"symbol": "NOSTRIKE", "strike": None, "option_type": "call"},
        {"symbol": "BADSTRIKE", "strike": "abc", "option_type": "call"},
        {"symbol": "BADTYPE", "strike": 600.0, "option_type": "warrant"},
        {"symbol": "NEGATIVE", "strike": -5.0, "option_type": "put"},
    ]
    window = select_atm_window(chain, spot=600.0, strikes_each_side=10)
    assert window.symbols == ["OK"]


def test_empty_chain_returns_empty_window() -> None:
    window = select_atm_window([], spot=600.0, strikes_each_side=10)
    assert window.atm_strike is None
    assert window.contracts == []


def test_zero_strikes_each_side_returns_only_atm() -> None:
    window = select_atm_window(make_chain(), spot=600.0, strikes_each_side=0)
    assert window.strikes == [600.0]
    assert len(window.contracts) == 2


def test_negative_strikes_each_side_is_rejected() -> None:
    with pytest.raises(ValueError):
        select_atm_window(make_chain(), spot=600.0, strikes_each_side=-1)


def test_half_dollar_strikes_are_supported() -> None:
    chain = make_chain(center=100.0, strikes_each_side=6, step=0.5)
    window = select_atm_window(chain, spot=100.2, strikes_each_side=2)
    assert window.atm_strike == 100.0
    assert window.strikes == [99.0, 99.5, 100.0, 100.5, 101.0]
