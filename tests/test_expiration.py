from __future__ import annotations

from datetime import date

import pytest

from tradier_collector.expiration import ExpirationResolver
from tradier_collector.tradier_client import TradierAPIError


class StubClient:
    def __init__(self, responses: dict[str, list[list[str]]]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[str] = []

    def get_expirations(self, symbol: str, include_all_roots: bool = True) -> list[str]:
        self.calls.append(symbol)
        queue = self.responses.get(symbol, [[]])
        return queue.pop(0) if len(queue) > 1 else queue[0]


class FailingClient:
    def get_expirations(self, symbol: str, include_all_roots: bool = True) -> list[str]:
        raise TradierAPIError("boom", endpoint="/markets/options/expirations", status=500)


TODAY = date(2026, 9, 15)


def test_zero_dte_present() -> None:
    client = StubClient({"SPY": [["2026-09-15", "2026-09-16"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    assert resolver.zero_dte("SPY", TODAY) == "2026-09-15"


def test_no_zero_dte_returns_none_and_refreshes_once() -> None:
    client = StubClient({"IWM": [["2026-09-18"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    assert resolver.zero_dte("IWM", TODAY) is None
    # One cached call plus exactly one forced refresh.
    assert client.calls == ["IWM", "IWM"]


def test_refresh_finds_a_newly_listed_expiration() -> None:
    client = StubClient({"QQQ": [["2026-09-16"], ["2026-09-15", "2026-09-16"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    assert resolver.zero_dte("QQQ", TODAY) == "2026-09-15"


def test_results_are_cached_per_day() -> None:
    client = StubClient({"SPY": [["2026-09-15"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    resolver.zero_dte("SPY", TODAY)
    resolver.zero_dte("SPY", TODAY)
    resolver.zero_dte("SPY", TODAY)
    assert client.calls == ["SPY"]


def test_cache_resets_on_a_new_trading_day() -> None:
    client = StubClient({"SPY": [["2026-09-15"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    resolver.zero_dte("SPY", TODAY)
    resolver.zero_dte("SPY", date(2026, 9, 16))
    assert len(client.calls) >= 2
    # Yesterday must not linger in the cache.
    assert all(key[1] == date(2026, 9, 16) for key in resolver._cache)


def test_api_errors_propagate() -> None:
    resolver = ExpirationResolver(FailingClient())  # type: ignore[arg-type]
    with pytest.raises(TradierAPIError):
        resolver.zero_dte("SPY", TODAY)


def test_clear_empties_the_cache() -> None:
    client = StubClient({"SPY": [["2026-09-15"]]})
    resolver = ExpirationResolver(client)  # type: ignore[arg-type]
    resolver.zero_dte("SPY", TODAY)
    resolver.clear()
    resolver.zero_dte("SPY", TODAY)
    assert client.calls == ["SPY", "SPY"]
