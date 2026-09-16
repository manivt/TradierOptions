from __future__ import annotations

from datetime import UTC, date, datetime

from config import Settings
from tests.test_collector import DAY, TS, FakeClient, build
from tradier_collector.cycle import make_cycle_id, run_cycle
from tradier_collector.tradier_client import TradierAPIError


class SelectiveClient(FakeClient):
    """Fails only for the tickers named in ``broken``."""

    def __init__(self, broken: set[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.broken = broken

    def get_quote(self, symbol: str, greeks: bool = True) -> dict[str, object]:
        if symbol in self.broken:
            raise TradierAPIError("nope", endpoint="/markets/quotes", status=500)
        return super().get_quote(symbol, greeks)


def test_all_tickers_succeed(settings: Settings) -> None:
    collector = build(settings, FakeClient())
    result = run_cycle(
        collector, tickers=("SPY", "QQQ"), poll_timestamp_utc=TS, trading_date=DAY
    )
    assert result.successful_tickers == ["SPY", "QQQ"]
    assert result.failed_tickers == []
    assert result.option_rows_saved == 84
    assert result.tracked_contract_count == 84


def test_one_ticker_failing_does_not_stop_the_others(settings: Settings) -> None:
    collector = build(settings, SelectiveClient({"SPY"}))
    result = run_cycle(
        collector, tickers=("SPY", "QQQ"), poll_timestamp_utc=TS, trading_date=DAY
    )
    assert result.failed_tickers == ["SPY"]
    assert result.successful_tickers == ["QQQ"]
    assert result.option_rows_saved == 42


def test_all_tickers_failing(settings: Settings) -> None:
    collector = build(settings, SelectiveClient({"SPY", "QQQ"}))
    result = run_cycle(
        collector, tickers=("SPY", "QQQ"), poll_timestamp_utc=TS, trading_date=DAY
    )
    assert result.failed_tickers == ["SPY", "QQQ"]
    assert result.option_rows_saved == 0


def test_cycle_without_zero_dte(settings: Settings) -> None:
    collector = build(settings, FakeClient(expirations=["2026-09-18"]))
    result = run_cycle(
        collector, tickers=("SPY", "QQQ"), poll_timestamp_utc=TS, trading_date=DAY
    )
    assert result.skipped_tickers == ["SPY", "QQQ"]
    assert result.failed_tickers == []


def test_partial_batch_success_is_recorded(settings: Settings) -> None:
    small = Settings(**{**settings.__dict__, "quote_batch_size": 10})
    collector = build(small, FakeClient(batch_failures={1}))
    result = run_cycle(collector, tickers=("SPY",), poll_timestamp_utc=TS, trading_date=DAY)
    assert result.batch_failures == 1
    assert result.successful_tickers == ["SPY"]


def test_every_ticker_shares_one_cycle_id_and_timestamp(settings: Settings) -> None:
    collector = build(settings, FakeClient())
    result = run_cycle(
        collector, tickers=("SPY", "QQQ"), poll_timestamp_utc=TS, trading_date=DAY
    )
    from tradier_collector.storage import load_option_day

    for ticker in ("SPY", "QQQ"):
        frame = load_option_day(settings.data_dir, ticker, DAY)
        assert set(frame["cycle_id"]) == {result.cycle_id}
        assert frame["poll_timestamp_utc"].nunique() == 1


def test_cycle_ids_are_unique_and_sortable() -> None:
    first = make_cycle_id(datetime(2026, 9, 15, 13, 30, tzinfo=UTC))
    second = make_cycle_id(datetime(2026, 9, 15, 13, 31, tzinfo=UTC))
    assert first != second
    assert first < second
    assert first.startswith("20260915T133000Z-")


def test_cycle_summary_dict_is_serialisable(settings: Settings) -> None:
    collector = build(settings, FakeClient())
    result = run_cycle(collector, tickers=("SPY",), poll_timestamp_utc=TS, trading_date=DAY)
    payload = result.as_dict()
    assert payload["trading_date"] == date(2026, 9, 15).isoformat()
    assert payload["tickers"][0]["ticker"] == "SPY"
