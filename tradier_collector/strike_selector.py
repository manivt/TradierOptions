"""ATM strike-window selection over a full option chain.

Rules (deliberately explicit, because backtests depend on them):

1. Extract unique numeric strikes from the chain.
2. Sort them ascending.
3. Find the strike closest to spot.
4. On an exact midpoint tie, always choose the **lower** strike.
5. Take the ATM strike plus N strikes below and N above where available.
6. Include both calls and puts for every selected strike.

If the window is truncated at an edge, return what exists and warn: a
truncated window is a data-quality fact worth recording, not a crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .normalization import to_float

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrikeWindow:
    """The outcome of one discovery pass over a chain."""

    atm_strike: float | None
    strikes: list[float] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    truncated_low: bool = False
    truncated_high: bool = False

    @property
    def symbols(self) -> list[str]:
        return [str(c["symbol"]) for c in self.contracts]


def _valid_entries(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chain rows that have a usable symbol, strike and option type."""
    valid: list[dict[str, Any]] = []
    for entry in chain:
        symbol = entry.get("symbol")
        strike = to_float(entry.get("strike"))
        option_type = entry.get("option_type")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if strike is None or strike <= 0:
            continue
        if not isinstance(option_type, str) or option_type.lower() not in {"call", "put"}:
            continue
        valid.append(entry)
    dropped = len(chain) - len(valid)
    if dropped:
        logger.warning("Ignored %d malformed chain entries", dropped)
    return valid


def find_atm_strike(strikes: list[float], spot: float) -> float | None:
    """Closest strike to spot; ties resolve to the lower strike."""
    if not strikes:
        return None
    ordered = sorted(set(strikes))
    best = ordered[0]
    best_distance = abs(best - spot)
    for strike in ordered[1:]:
        distance = abs(strike - spot)
        # Strict inequality keeps the earlier (lower) strike on an exact tie.
        if distance < best_distance - 1e-9:
            best = strike
            best_distance = distance
    return best


def select_atm_window(
    chain: list[dict[str, Any]],
    spot: float,
    strikes_each_side: int = 10,
) -> StrikeWindow:
    """Select the ATM +/- N strike window from a full chain."""
    if strikes_each_side < 0:
        raise ValueError("strikes_each_side must be >= 0")

    entries = _valid_entries(chain)
    if not entries:
        logger.warning("Chain contained no usable contracts; discovery window is empty")
        return StrikeWindow(atm_strike=None)

    strikes = sorted({float(to_float(e.get("strike")) or 0.0) for e in entries})
    atm = find_atm_strike(strikes, spot)
    if atm is None:  # pragma: no cover - guarded by the emptiness check above
        return StrikeWindow(atm_strike=None)

    index = strikes.index(atm)
    low_index = index - strikes_each_side
    high_index = index + strikes_each_side
    truncated_low = low_index < 0
    truncated_high = high_index > len(strikes) - 1
    selected = strikes[max(0, low_index) : min(len(strikes), high_index + 1)]
    selected_set = set(selected)

    if truncated_low or truncated_high:
        logger.warning(
            "ATM window truncated at %s edge for spot %.4f: wanted %d strikes, got %d "
            "(chain has %d strikes)",
            "lower" if truncated_low and not truncated_high else
            "upper" if truncated_high and not truncated_low else "both",
            spot,
            2 * strikes_each_side + 1,
            len(selected),
            len(strikes),
        )

    contracts = [e for e in entries if to_float(e.get("strike")) in selected_set]

    # A strike missing one side is worth knowing about but is never fatal.
    by_strike: dict[float, set[str]] = {}
    for contract in contracts:
        strike_value = float(to_float(contract.get("strike")) or 0.0)
        option_type = str(contract.get("option_type", "")).lower()
        by_strike.setdefault(strike_value, set()).add(option_type)
    incomplete = sorted(s for s, kinds in by_strike.items() if kinds != {"call", "put"})
    if incomplete:
        logger.warning("Strikes without both a call and a put: %s", incomplete)

    return StrikeWindow(
        atm_strike=atm,
        strikes=selected,
        contracts=contracts,
        truncated_low=truncated_low,
        truncated_high=truncated_high,
    )
