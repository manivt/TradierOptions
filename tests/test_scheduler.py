from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from config import Settings
from scheduler import CollectorScheduler
from tests.test_collector import FakeClient, build

SESSION_OPEN = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


class FakeTime:
    """A controllable clock: sleeping simply advances it."""

    def __init__(self, start: datetime, *, work_seconds: float = 0.0) -> None:
        self.now = start
        self.work_seconds = work_seconds
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_scheduler(
    settings: Settings, fake_time: FakeTime, *, client: FakeClient | None = None
) -> CollectorScheduler:
    client = client or FakeClient()
    collector = build(settings, client)
    scheduler = CollectorScheduler(
        settings,
        client=client,  # type: ignore[arg-type]
        collector=collector,
        now=fake_time,
        sleep=fake_time.sleep,
    )
    # Every cycle consumes wall-clock time, like the real collector.
    original = collector.collect

    def timed_collect(ticker: str, ctx: object) -> object:
        fake_time.advance(fake_time.work_seconds)
        return original(ticker, ctx)  # type: ignore[arg-type]

    collector.collect = timed_collect  # type: ignore[assignment]
    return scheduler


def test_polls_land_on_absolute_boundaries(settings: Settings) -> None:
    fake = FakeTime(SESSION_OPEN + timedelta(seconds=7), work_seconds=3.0)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=3, install_signals=False)

    from tradier_collector.storage import load_option_day

    frame = load_option_day(settings.data_dir, "SPY", SESSION_OPEN.date())
    stamps = sorted(set(frame["poll_timestamp_utc"]))
    assert [s.second for s in stamps] == [0, 0, 0]
    assert [s.minute for s in stamps] == [31, 32, 33]


def test_no_cumulative_drift_when_cycles_are_slow(settings: Settings) -> None:
    # Each cycle takes 20 seconds of wall clock across two tickers.
    fake = FakeTime(SESSION_OPEN, work_seconds=10.0)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=5, install_signals=False)

    from tradier_collector.storage import load_option_day

    frame = load_option_day(settings.data_dir, "SPY", SESSION_OPEN.date())
    stamps = sorted(set(frame["poll_timestamp_utc"]))
    deltas = {
        (b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)
    }
    assert stamps[0] == SESSION_OPEN
    assert deltas == {60.0}


def test_overrun_skips_a_boundary_instead_of_catching_up(settings: Settings) -> None:
    # 45 seconds per ticker: every cycle overruns the 60-second interval.
    fake = FakeTime(SESSION_OPEN, work_seconds=45.0)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=3, install_signals=False)

    from tradier_collector.storage import load_option_day

    frame = load_option_day(settings.data_dir, "SPY", SESSION_OPEN.date())
    stamps = sorted(set(frame["poll_timestamp_utc"]))
    deltas = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)]
    assert all(d >= 120.0 for d in deltas)
    assert all(s.second == 0 for s in stamps)


def test_starting_before_the_open_waits(settings: Settings) -> None:
    fake = FakeTime(SESSION_OPEN - timedelta(minutes=30))
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=1, install_signals=False)
    assert fake.now >= SESSION_OPEN

    from tradier_collector.storage import load_option_day

    frame = load_option_day(settings.data_dir, "SPY", SESSION_OPEN.date())
    assert set(frame["poll_timestamp_utc"]) == {SESSION_OPEN}


def test_starting_after_the_close_waits_for_the_next_session(settings: Settings) -> None:
    after_close = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
    fake = FakeTime(after_close)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=1, install_signals=False)
    # It waited overnight rather than collecting outside the window.
    assert fake.now.astimezone(scheduler.clock.tz).date().isoformat() == "2026-09-16"


def test_weekend_start_waits_for_monday(settings: Settings) -> None:
    saturday = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    fake = FakeTime(saturday)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=1, install_signals=False)
    assert fake.now.astimezone(scheduler.clock.tz).date().isoformat() == "2026-09-14"


def test_graceful_shutdown_stops_the_loop(settings: Settings) -> None:
    fake = FakeTime(SESSION_OPEN)
    scheduler = make_scheduler(settings, fake)
    scheduler.request_stop()
    assert scheduler.run(max_cycles=10, install_signals=False) == 0
    assert scheduler.stopping


def test_signal_handler_requests_a_stop(settings: Settings) -> None:
    fake = FakeTime(SESSION_OPEN)
    scheduler = make_scheduler(settings, fake)
    scheduler._handle_signal(15, None)
    assert scheduler.stopping


def test_health_file_is_written_and_finalised(settings: Settings) -> None:
    import json

    fake = FakeTime(SESSION_OPEN, work_seconds=1.0)
    scheduler = make_scheduler(settings, fake)
    scheduler.run(max_cycles=2, install_signals=False)

    path = settings.data_dir / "health" / "2026-09-15.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["collector_finished_utc"] is not None
    assert payload["expected_cycles"] == 406
    assert payload["tickers"]["SPY"]["observed_cycles"] == 2
    assert payload["tickers"]["SPY"]["contracts_discovered"] == 42


def test_restart_mid_session_preserves_morning_counters(settings: Settings) -> None:
    import json

    fake = FakeTime(SESSION_OPEN, work_seconds=1.0)
    make_scheduler(settings, fake).run(max_cycles=2, install_signals=False)

    later = FakeTime(SESSION_OPEN + timedelta(hours=2), work_seconds=1.0)
    make_scheduler(settings, later).run(max_cycles=2, install_signals=False)

    payload = json.loads(
        (settings.data_dir / "health" / "2026-09-15.json").read_text(encoding="utf-8")
    )
    assert payload["tickers"]["SPY"]["observed_cycles"] == 4


@pytest.mark.parametrize("interval", [60, 300])
def test_run_immediate_cycle_ignores_the_session_window(settings: Settings, interval: int) -> None:
    tweaked = Settings(**{**settings.__dict__, "poll_interval_seconds": interval})
    fake = FakeTime(datetime(2026, 9, 12, 15, 0, tzinfo=UTC))  # a Saturday
    scheduler = make_scheduler(tweaked, fake)
    result = scheduler.run_immediate_cycle()
    # It ran outside the session window: the underlying was still sampled, and
    # the missing same-day expiration is a skip rather than a failure.
    assert result.failed_tickers == []
    assert result.skipped_tickers == ["SPY", "QQQ"]
    assert all(r.underlying_saved == 1 for r in result.results)
