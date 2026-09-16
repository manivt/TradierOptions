"""Thin, defensive Tradier REST client.

Design notes
------------
* One persistent :class:`requests.Session` (connection reuse matters on an
  e2-micro with a 60-second duty cycle).
* Tradier wraps payloads inconsistently: a container key may hold ``null``, a
  single object, or a list.  Every public method normalises that here so the
  rest of the collector can rely on predictable types.
* Retries are a small explicit loop rather than a decorator.  We need to look
  at ``Retry-After`` and the ``X-Ratelimit-*`` headers, and we must retry a
  bounded number of times (never unbounded).
* Exceptions never carry the API token, and response bodies are truncated and
  scrubbed before being attached to an error.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from typing import Any, cast

import requests

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Client errors that are never worth retrying.
NON_RETRY_STATUSES = frozenset({400, 401, 403, 404, 422})

MAX_BODY_CHARS = 500
RATELIMIT_HEADERS = (
    "Retry-After",
    "X-Ratelimit-Allowed",
    "X-Ratelimit-Used",
    "X-Ratelimit-Available",
    "X-Ratelimit-Expiry",
)

_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._\-]+"
    r"|(?:token|access_token|api[_-]?key)\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]+)"
)


class TradierAPIError(Exception):
    """Raised for any non-recoverable Tradier API failure."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.status = status
        self.body = body
        detail = f"{message} (endpoint={endpoint}, status={status})"
        if body:
            detail = f"{detail}: {body}"
        super().__init__(detail)


def sanitize_body(body: str | None) -> str:
    """Truncate and scrub a response body so it is safe to log or raise."""
    if not body:
        return ""
    scrubbed = _SECRET_PATTERN.sub("***redacted***", body)
    scrubbed = scrubbed.replace("\n", " ").strip()
    if len(scrubbed) > MAX_BODY_CHARS:
        scrubbed = scrubbed[:MAX_BODY_CHARS] + "...[truncated]"
    return scrubbed


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Normalise the Tradier null / object / list container shapes."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [cast(dict[str, Any], value)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


class TradierClient:
    """Minimal read-only Tradier market-data client."""

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = "https://api.tradier.com/v1",
        timeout: tuple[float, float] = (5.0, 20.0),
        max_attempts: int = 3,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not api_token:
            raise ValueError("api_token must not be empty")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "tradier-0dte-collector/0.1",
            }
        )

    # ------------------------------------------------------------------ core

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> TradierClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _log_ratelimit(self, endpoint: str, response: requests.Response) -> None:
        present = {h: response.headers[h] for h in RATELIMIT_HEADERS if h in response.headers}
        if present:
            logger.warning("Tradier rate-limit headers for %s: %s", endpoint, present)

    def _retry_delay(self, attempt: int, response: requests.Response | None) -> float:
        """Exponential 1s / 2s / 4s, overridden by Retry-After when sane."""
        delay = float(2 ** (attempt - 1))
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    suggested = float(raw)
                except ValueError:
                    suggested = -1.0
                # Ignore absurd values so a bad header cannot stall the session.
                if 0 <= suggested <= 120:
                    delay = max(delay, suggested)
        return delay

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            response: requests.Response | None = None
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise TradierAPIError(
                        f"Request failed after {attempt} attempts: {type(exc).__name__}",
                        endpoint=endpoint,
                    ) from exc
                delay = self._retry_delay(attempt, None)
                logger.warning(
                    "Transport error on %s (attempt %d/%d): %s; retrying in %.1fs",
                    endpoint,
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    delay,
                )
                self._sleep(delay)
                continue

            status = response.status_code
            if status == 429:
                self._log_ratelimit(endpoint, response)

            if status in RETRY_STATUSES:
                body = sanitize_body(response.text)
                if attempt >= self.max_attempts:
                    raise TradierAPIError(
                        f"Retryable status persisted after {attempt} attempts",
                        endpoint=endpoint,
                        status=status,
                        body=body,
                    )
                delay = self._retry_delay(attempt, response)
                logger.warning(
                    "HTTP %d on %s (attempt %d/%d); retrying in %.1fs",
                    status,
                    endpoint,
                    attempt,
                    self.max_attempts,
                    delay,
                )
                self._sleep(delay)
                continue

            if status >= 400:
                raise TradierAPIError(
                    "Non-retryable HTTP error",
                    endpoint=endpoint,
                    status=status,
                    body=sanitize_body(response.text),
                )

            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise TradierAPIError(
                    "Response was not valid JSON",
                    endpoint=endpoint,
                    status=status,
                    body=sanitize_body(response.text),
                ) from exc

            if payload is None:
                return {}
            if not isinstance(payload, dict):
                raise TradierAPIError(
                    f"Expected a JSON object, got {type(payload).__name__}",
                    endpoint=endpoint,
                    status=status,
                )
            return cast(dict[str, Any], payload)

        # Defensive: the loop above always returns or raises.
        raise TradierAPIError(  # pragma: no cover
            f"Request exhausted retries: {last_error!r}", endpoint=endpoint
        )

    # --------------------------------------------------------------- public

    def get_quotes(self, symbols: Sequence[str], greeks: bool = True) -> list[dict[str, Any]]:
        """Quotes for one or many symbols.  Always returns a list."""
        cleaned = [s for s in (str(sym).strip() for sym in symbols) if s]
        if not cleaned:
            return []
        payload = self._request(
            "/markets/quotes",
            {"symbols": ",".join(cleaned), "greeks": "true" if greeks else "false"},
        )
        quotes = payload.get("quotes")
        if not isinstance(quotes, dict):
            return []
        unmatched = quotes.get("unmatched_symbols")
        if unmatched:
            logger.warning("Tradier reported unmatched symbols: %s", unmatched)
        return _as_list(quotes.get("quote"))

    def get_quote(self, symbol: str, greeks: bool = True) -> dict[str, Any]:
        """A single quote.  Raises :class:`TradierAPIError` if absent."""
        quotes = self.get_quotes([symbol], greeks=greeks)
        if not quotes:
            raise TradierAPIError(
                f"No quote returned for symbol {symbol!r}", endpoint="/markets/quotes"
            )
        return quotes[0]

    def get_expirations(self, symbol: str, include_all_roots: bool = True) -> list[str]:
        """Expiration dates as YYYY-MM-DD strings.  Always returns a list."""
        payload = self._request(
            "/markets/options/expirations",
            {
                "symbol": symbol,
                "includeAllRoots": "true" if include_all_roots else "false",
                "strikes": "false",
            },
        )
        expirations = payload.get("expirations")
        if not isinstance(expirations, dict):
            return []
        raw = expirations.get("date")
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item) for item in raw if isinstance(item, str | int)]
        return []

    def get_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        """Full option chain for one expiration.  Always returns a list."""
        payload = self._request(
            "/markets/options/chains",
            {
                "symbol": symbol,
                "expiration": expiration,
                "greeks": "true" if greeks else "false",
            },
        )
        options = payload.get("options")
        if not isinstance(options, dict):
            return []
        return _as_list(options.get("option"))


def batched(items: Sequence[str], size: int) -> Iterable[list[str]]:
    """Yield items in chunks of at most size."""
    if size < 1:
        raise ValueError("size must be >= 1")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])
