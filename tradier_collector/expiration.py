"""Same-day (0DTE) expiration resolution.

Not every configured ticker has a same-day expiration on every session, and
that is a normal condition, not a failure: it is logged clearly, the ticker is
skipped for the cycle, and the scheduler keeps running.

The expiration list changes at most once per day, so it is cached per
(ticker, trading date).  The cache resets automatically when the New York date
rolls over, which also makes a long-running process safe across midnight.
"""

from __future__ import annotations

import logging
from datetime import date

from .tradier_client import TradierAPIError, TradierClient

logger = logging.getLogger(__name__)


class ExpirationResolver:
    """Resolves and caches the 0DTE expiration for each ticker."""

    def __init__(self, client: TradierClient) -> None:
        self._client = client
        self._cache: dict[tuple[str, date], list[str]] = {}

    def expirations(self, ticker: str, trading_date: date, *, refresh: bool = False) -> list[str]:
        """All expirations for ``ticker``, cached for the given trading date."""
        key = (ticker, trading_date)
        if refresh or key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if k[1] == trading_date}
            self._cache[key] = self._client.get_expirations(ticker)
        return self._cache[key]

    def zero_dte(self, ticker: str, trading_date: date) -> str | None:
        """The same-day expiration string, or None when the ticker has no 0DTE.

        A miss is retried once with a forced refresh: an expiration list cached
        moments before a new listing appeared is the only benign explanation,
        and the extra request happens at most once per ticker per day.
        """
        wanted = trading_date.isoformat()
        try:
            available = self.expirations(ticker, trading_date)
        except TradierAPIError:
            logger.exception("Failed to load expirations for %s", ticker)
            raise

        if wanted in available:
            return wanted

        available = self.expirations(ticker, trading_date, refresh=True)
        if wanted in available:
            return wanted

        logger.info(
            "No 0DTE expiration for %s on %s; skipping this ticker for the cycle "
            "(nearest listed: %s)",
            ticker,
            wanted,
            available[:3] if available else "none returned",
        )
        return None

    def clear(self) -> None:
        self._cache.clear()
