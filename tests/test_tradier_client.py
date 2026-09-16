from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from tradier_collector.tradier_client import (
    TradierAPIError,
    TradierClient,
    batched,
    sanitize_body,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is _INVALID:
            raise ValueError("No JSON object could be decoded")
        return self._payload


_INVALID = object()


class FakeSession:
    """Replays a scripted list of responses or exceptions."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any], timeout: Any) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, FakeResponse)
        return item

    def close(self) -> None:
        pass


def make_client(responses: list[Any]) -> tuple[TradierClient, FakeSession, list[float]]:
    session = FakeSession(responses)
    slept: list[float] = []
    client = TradierClient(
        "secret-token",
        session=session,  # type: ignore[arg-type]
        sleep=slept.append,
        max_attempts=3,
    )
    return client, session, slept


def test_headers_include_bearer_token_and_gzip() -> None:
    client, session, _ = make_client([])
    assert session.headers["Authorization"] == "Bearer secret-token"
    assert session.headers["Accept"] == "application/json"
    assert session.headers["Accept-Encoding"] == "gzip"
    client.close()


def test_get_quotes_success() -> None:
    payload = {"quotes": {"quote": [{"symbol": "SPY"}, {"symbol": "QQQ"}]}}
    client, session, _ = make_client([FakeResponse(payload=payload)])
    quotes = client.get_quotes(["SPY", "QQQ"])
    assert [q["symbol"] for q in quotes] == ["SPY", "QQQ"]
    assert session.calls[0]["params"]["symbols"] == "SPY,QQQ"


def test_get_quotes_normalises_single_object() -> None:
    client, _, _ = make_client([FakeResponse(payload={"quotes": {"quote": {"symbol": "SPY"}}})])
    assert client.get_quotes(["SPY"]) == [{"symbol": "SPY"}]


def test_get_quotes_normalises_null() -> None:
    client, _, _ = make_client([FakeResponse(payload={"quotes": {"quote": None}})])
    assert client.get_quotes(["NOPE"]) == []


def test_get_quotes_with_no_symbols_makes_no_request() -> None:
    client, session, _ = make_client([])
    assert client.get_quotes([]) == []
    assert session.calls == []


def test_get_quote_raises_when_absent() -> None:
    client, _, _ = make_client([FakeResponse(payload={"quotes": "null"})])
    with pytest.raises(TradierAPIError):
        client.get_quote("BADSYM")


def test_get_expirations_handles_all_shapes() -> None:
    client, _, _ = make_client(
        [
            FakeResponse(payload={"expirations": {"date": ["2026-09-15", "2026-09-16"]}}),
            FakeResponse(payload={"expirations": {"date": "2026-09-15"}}),
            FakeResponse(payload={"expirations": "null"}),
        ]
    )
    assert client.get_expirations("SPY") == ["2026-09-15", "2026-09-16"]
    assert client.get_expirations("SPY") == ["2026-09-15"]
    assert client.get_expirations("SPY") == []


def test_get_chain_handles_all_shapes() -> None:
    client, _, _ = make_client(
        [
            FakeResponse(payload={"options": {"option": [{"symbol": "A"}, {"symbol": "B"}]}}),
            FakeResponse(payload={"options": {"option": {"symbol": "A"}}}),
            FakeResponse(payload={"options": None}),
        ]
    )
    assert len(client.get_chain("SPY", "2026-09-15")) == 2
    assert len(client.get_chain("SPY", "2026-09-15")) == 1
    assert client.get_chain("SPY", "2026-09-15") == []


def test_timeout_is_retried_then_raises() -> None:
    client, session, slept = make_client(
        [requests.exceptions.ConnectTimeout("t1"), requests.exceptions.ReadTimeout("t2"),
         requests.exceptions.ReadTimeout("t3")]
    )
    with pytest.raises(TradierAPIError) as excinfo:
        client.get_quotes(["SPY"])
    assert len(session.calls) == 3
    assert slept == [1.0, 2.0]
    assert "secret-token" not in str(excinfo.value)


def test_connection_failure_then_success() -> None:
    client, session, slept = make_client(
        [
            requests.exceptions.ConnectionError("boom"),
            FakeResponse(payload={"quotes": {"quote": {"symbol": "SPY"}}}),
        ]
    )
    assert client.get_quotes(["SPY"]) == [{"symbol": "SPY"}]
    assert len(session.calls) == 2
    assert slept == [1.0]


def test_401_is_not_retried() -> None:
    client, session, _ = make_client(
        [FakeResponse(status_code=401, payload={"fault": "unauthorised"})]
    )
    with pytest.raises(TradierAPIError) as excinfo:
        client.get_quotes(["SPY"])
    assert excinfo.value.status == 401
    assert excinfo.value.endpoint == "/markets/quotes"
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [400, 403, 404])
def test_other_client_errors_are_not_retried(status: int) -> None:
    client, session, _ = make_client([FakeResponse(status_code=status, payload={})])
    with pytest.raises(TradierAPIError):
        client.get_quotes(["SPY"])
    assert len(session.calls) == 1


def test_429_then_success_respects_retry_after() -> None:
    client, session, slept = make_client(
        [
            FakeResponse(
                status_code=429,
                payload={},
                headers={
                    "Retry-After": "3",
                    "X-Ratelimit-Allowed": "120",
                    "X-Ratelimit-Used": "120",
                    "X-Ratelimit-Available": "0",
                    "X-Ratelimit-Expiry": "1789500000000",
                },
            ),
            FakeResponse(payload={"quotes": {"quote": {"symbol": "SPY"}}}),
        ]
    )
    assert client.get_quotes(["SPY"]) == [{"symbol": "SPY"}]
    assert slept == [3.0]
    assert len(session.calls) == 2


def test_absurd_retry_after_is_ignored() -> None:
    client, _, slept = make_client(
        [
            FakeResponse(status_code=503, payload={}, headers={"Retry-After": "86400"}),
            FakeResponse(payload={"quotes": {"quote": {"symbol": "SPY"}}}),
        ]
    )
    client.get_quotes(["SPY"])
    assert slept == [1.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_retry_then_succeed(status: int) -> None:
    client, session, _ = make_client(
        [
            FakeResponse(status_code=status, payload={}),
            FakeResponse(payload={"quotes": {"quote": {"symbol": "SPY"}}}),
        ]
    )
    assert client.get_quotes(["SPY"]) == [{"symbol": "SPY"}]
    assert len(session.calls) == 2


def test_retries_are_bounded() -> None:
    client, session, slept = make_client([FakeResponse(status_code=500, payload={})] * 3)
    with pytest.raises(TradierAPIError):
        client.get_quotes(["SPY"])
    assert len(session.calls) == 3
    assert slept == [1.0, 2.0]


def test_invalid_json_raises_api_error() -> None:
    client, _, _ = make_client([FakeResponse(payload=_INVALID, text="<html>nope</html>")])
    with pytest.raises(TradierAPIError, match="not valid JSON"):
        client.get_quotes(["SPY"])


def test_non_object_json_raises() -> None:
    client, _, _ = make_client([FakeResponse(payload=[1, 2, 3])])
    with pytest.raises(TradierAPIError, match="Expected a JSON object"):
        client.get_quotes(["SPY"])


def test_sanitize_body_redacts_and_truncates() -> None:
    body = '{"access_token": "abcdef123456", "note": "' + "x" * 900 + '"}'
    cleaned = sanitize_body(body)
    assert "abcdef123456" not in cleaned
    assert "***redacted***" in cleaned
    assert cleaned.endswith("...[truncated]")
    assert sanitize_body(None) == ""


def test_batched_chunks_evenly() -> None:
    assert list(batched(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
    assert list(batched([], 5)) == []
    with pytest.raises(ValueError):
        list(batched(["a"], 0))
