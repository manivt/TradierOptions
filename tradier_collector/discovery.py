"""Chain discovery: the bridge between a full chain and the sticky universe.

The full-chain request exists only to discover strikes that have become
relevant.  It is deliberately simple.  Discovery frequency is configurable
(``DISCOVERY_INTERVAL_CYCLES``) so it can be reduced later without touching
any other component; the sticky universe means a skipped discovery cycle can
only delay picking up a brand-new strike, never lose an existing one.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .contract_tracker import ContractTracker, TrackedContract
from .strike_selector import StrikeWindow, select_atm_window
from .tradier_client import TradierClient

logger = logging.getLogger(__name__)


def discover_window(
    client: TradierClient,
    *,
    ticker: str,
    expiration: str,
    spot: float,
    strikes_each_side: int,
) -> StrikeWindow:
    """Fetch the same-day chain and select the ATM window."""
    chain = client.get_chain(ticker, expiration, greeks=True)
    if not chain:
        logger.warning("Empty chain returned for %s %s", ticker, expiration)
        return StrikeWindow(atm_strike=None)
    window = select_atm_window(chain, spot, strikes_each_side)
    logger.debug(
        "%s discovery: spot=%.4f atm=%s strikes=%d contracts=%d",
        ticker,
        spot,
        window.atm_strike,
        len(window.strikes),
        len(window.contracts),
    )
    return window


def apply_discovery(
    tracker: ContractTracker,
    window: StrikeWindow,
    *,
    expiration: str,
    discovered_utc: datetime,
) -> list[TrackedContract]:
    """Merge a discovery window into the sticky universe."""
    if not window.contracts:
        return []
    new = tracker.add_contracts(
        window.contracts, expiration=expiration, discovered_utc=discovered_utc
    )
    if new:
        logger.info(
            "%s: %d new contracts tracked (universe now %d)",
            tracker.ticker,
            len(new),
            len(tracker),
        )
    return new
