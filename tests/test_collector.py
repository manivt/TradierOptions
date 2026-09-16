from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from config import Settings
from tests.conftest import make_chain, make_equity_quote, make_option_quote
from tradier_collector.collector import CycleContext, TickerCollector
from tradier_collector.expiration import ExpirationResolver
from tradier_collector.storage import load_option_day, load_underlying_day
from tradier_collector.tradier_client import TradierAPIError

DAY = date(2026, 9, 15)
TS = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


class FakeClient:
    """A scriptable stand-in for TradierClient."""

    def __init__(
        self,
        *,
        spot: float = 600.25,
        expirations: list[str] | None = None,
        chain: list[dict[str, Any]] | None = None,
        batch_failures: set[int] | None = None,
        drop_symbols: set[str] | None = None,
        quote_error: Exception | None = None,
    ) -> None:
        self.spot = spot
        self.expirations = expirations if expirations is not None else ["2026-09-15"]
        self.chain = chain if chain is not None else make_chain()
        self.batch_failures = batch_failures or set()
        self.drop_symbols = drop_symbols or set()
        self.quote_error = quote_error
        self.batch_calls = 0
        self.chain_calls = 0

    def get_quote(self, symbol: str, greeks: bool = True) -> dict[str, Any]:
        if self.quote_error is not None:
            raise self.quote_error
        return make_equity_quote(symbol=symbol, last=self.spot)

    def get_expirations(self, symbol: str, include_all_roots: bool = True) -> list[str]:
        return list(self.expirations)

    def get_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        self.chain_calls += 1
        return list(self.chain)

    def get_quotes(self, symbols: list[str], greeks: bool = True) -> list[dict[str, Any]]:
        index = self.batch_calls
        self.batch_calls += 1
        if index in self.batch_failures:
            raise TradierAPIError("batch failed", endpoint="/markets/quotes", status=503)
        lookup = {c["symbol"]: c for c in self.chain}
        return [
            lookup.get(symbol, make_option_quote(symbol=symbol))
            for symbol in symbols
            if symbol not in self.drop_symbols
        ]


def build(settings: Settings, client: FakeClient) -> TickerCollector:
    return TickerCollector(settings, client, ExpirationResolver(client))  # type: ignore[arg-type]


def context(cycle: str = "cyc-1", index: int = 0) -> CycleContext:
    return CycleContext(
        cycle_id=cycle, poll_timestamp_utc=TS, trading_date=DAY, cycle_index=index
    )


def test_single_batch_collection(settings: Settings) -> None:
    client = FakeClient()
    collector = build(settings, client)
    result = collector.collect("SPY", context())
    assert result.success
    assert result.underlying_saved == 1
    assert result.new_contracts_discovered == 42
    assert result.tracked_contract_count == 42
    assert result.option_rows_saved == 42
    assert result.batch_failures == 0
    assert client.batch_calls == 1


def test_many_batches(settings: Settings) -> None:
    small = Settings(**{**settings.__dict__, "quote_batch_size": 10})
    client = FakeClient()
    collector = build(small, client)
    result = collector.collect("SPY", context())
    assert result.option_rows_saved == 42
    assert client.batch_calls == 5


def test_one_batch_failure_keeps_the_rest(settings: Settings) -> None:
    small = Settings(**{**settings.__dict__, "quote_batch_size": 10})
    client = FakeClient(batch_failures={1})
    collector = build(small, client)
    result = collector.collect("SPY", context())
    assert result.success
    assert result.batch_failures == 1
    assert result.option_rows_saved == 32
    assert result.error is None


def test_all_batches_failing_is_reported_as_an_error(settings: Settings) -> None:
    client = FakeClient(batch_failures={0})
    collector = build(settings, client)
    result = collector.collect("SPY", context())
    assert not result.success
    assert result.error is not None
    assert "quote batches failed" in result.error


def test_missing_contract_does_not_fail_the_ticker(settings: Settings) -> None:
    dropped = "SPY260915C00600000"  # inside the ATM window, so it is tracked
    client = FakeClient(drop_symbols={dropped})
    collector = build(settings, client)
    result = collector.collect("SPY", context())
    assert result.success
    assert result.missing_symbols == 1
    assert result.option_rows_saved == 41


def test_no_zero_dte_skips_options_but_keeps_the_underlying(settings: Settings) -> None:
    client = FakeClient(expirations=["2026-09-18"])
    collector = build(settings, client)
    result = collector.collect("IWM", context())
    assert result.success
    assert result.skipped_reason == "no_0dte_expiration"
    assert result.underlying_saved == 1
    assert result.option_rows_saved == 0


def test_underlying_failure_is_contained(settings: Settings) -> None:
    client = FakeClient(quote_error=TradierAPIError("down", endpoint="/markets/quotes", status=500))
    collector = build(settings, client)
    result = collector.collect("SPY", context())
    assert not result.success
    assert result.error is not None
    assert result.elapsed_seconds >= 0


def test_empty_chain_yields_an_empty_universe(settings: Settings) -> None:
    client = FakeClient(chain=[])
    collector = build(settings, client)
    result = collector.collect("SPY", context())
    assert result.skipped_reason == "empty_universe"
    assert result.option_rows_saved == 0


def test_sticky_universe_survives_spot_moving_away(settings: Settings, tmp_path: Path) -> None:
    client = FakeClient(spot=600.0)
    collector = build(settings, client)
    collector.collect("SPY", context("cyc-1", 0))
    morning_symbols = set(collector.tracker("SPY", DAY).symbols())

    # Spot rallies; discovery now returns a higher window.
    client.spot = 607.0
    client.chain = make_chain(center=607.0)
    result = collector.collect("SPY", context("cyc-2", 1))

    tracked = set(collector.tracker("SPY", DAY).symbols())
    assert morning_symbols.issubset(tracked)
    assert len(tracked) > len(morning_symbols)
    assert result.option_rows_saved == len(tracked)

    frame = load_option_day(settings.data_dir, "SPY", DAY)
    later = frame[frame["cycle_id"] == "cyc-2"]
    assert set(morning_symbols).issubset(set(later["symbol"]))


def test_universe_is_restored_after_a_restart(settings: Settings) -> None:
    client = FakeClient()
    build(settings, client).collect("SPY", context("cyc-1", 0))

    fresh_collector = build(settings, FakeClient(chain=[]))
    result = fresh_collector.collect("SPY", context("cyc-2", 1))
    assert result.tracked_contract_count == 42
    assert result.option_rows_saved == 42


def test_discovery_interval_limits_chain_requests(settings: Settings) -> None:
    every_third = Settings(**{**settings.__dict__, "discovery_interval_cycles": 3})
    client = FakeClient()
    collector = build(every_third, client)
    for index in range(4):
        collector.collect("SPY", context(f"cyc-{index}", index))
    # Cycles 0 and 3 discover; 1 and 2 reuse the sticky universe.
    assert client.chain_calls == 2


def test_missing_spot_skips_discovery_but_keeps_quoting(settings: Settings) -> None:
    client = FakeClient()
    collector = build(settings, client)
    collector.collect("SPY", context("cyc-1", 0))

    class NoSpotClient(FakeClient):
        def get_quote(self, symbol: str, greeks: bool = True) -> dict[str, Any]:
            return {"symbol": symbol, "last": None, "bid": None, "ask": None}

    collector.client = NoSpotClient()  # type: ignore[assignment]
    result = collector.collect("SPY", context("cyc-2", 1))
    assert result.success
    assert result.spot is None
    assert result.new_contracts_discovered == 0
    assert result.option_rows_saved == 42


def test_underlying_rows_are_persisted(settings: Settings) -> None:
    collector = build(settings, FakeClient())
    collector.collect("SPY", context("cyc-1", 0))
    frame = load_underlying_day(settings.data_dir, "SPY", DAY)
    assert len(frame) == 1
    assert frame.iloc[0]["ticker"] == "SPY"
    assert frame.iloc[0]["cycle_id"] == "cyc-1"


def test_unexpected_exceptions_are_contained(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    collector = build(settings, FakeClient())

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("tradier_collector.collector.append_underlying_snapshot", boom)
    result = collector.collect("SPY", context())
    assert not result.success
    assert result.error is not None
    assert "RuntimeError" in result.error
