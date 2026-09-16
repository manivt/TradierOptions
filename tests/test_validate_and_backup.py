from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import backup
import validate_dataset
from config import Settings
from tests.conftest import make_chain
from tests.test_collector import FakeClient, build
from tradier_collector.collector import CycleContext
from tradier_collector.storage import health_path, option_path

DAY = date(2026, 9, 15)
OPEN = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


def collect_session(settings: Settings, cycles: int = 20) -> None:
    """Collect a short synthetic session where spot rallies away from the open."""
    client = FakeClient(spot=600.0)
    collector = build(settings, client)
    for index in range(cycles):
        if index == cycles // 2:
            client.spot = 615.0
            client.chain = make_chain(center=615.0)
        collector.collect(
            "SPY",
            CycleContext(
                cycle_id=f"cyc-{index:04d}",
                poll_timestamp_utc=OPEN + timedelta(minutes=index),
                trading_date=DAY,
                cycle_index=index,
            ),
        )


def test_validation_proves_sticky_tracking(settings: Settings) -> None:
    collect_session(settings)
    report = validate_dataset.validate(settings.data_dir, "SPY", DAY, settings)
    names = {check.name: check for check in report.checks}

    assert names["sticky contracts keep reporting all session"].passed
    assert names["out-of-window contracts still collected late in the session"].passed
    assert names["no duplicate cycle_id + symbol rows"].passed
    assert names["one poll timestamp per cycle_id"].passed
    assert names["vendor greek timestamps distinct from poll timestamps"].passed
    assert report.ok
    assert "ALL CHECKS PASSED" in report.render()


def test_validation_reports_missing_data(settings: Settings) -> None:
    report = validate_dataset.validate(settings.data_dir, "SPY", DAY, settings)
    assert not report.ok
    assert "CHECKS FAILED" in report.render()


def test_validation_detects_unaligned_cycles(settings: Settings) -> None:
    collect_session(settings, cycles=5)
    from tradier_collector.storage import append_option_snapshot

    append_option_snapshot(
        [
            {
                "cycle_id": "rogue",
                "poll_timestamp_utc": OPEN + timedelta(seconds=17),
                "ticker": "SPY",
                "expiration": "2026-09-15",
                "symbol": "SPY260915C00600000",
            }
        ],
        data_dir=settings.data_dir,
        ticker="SPY",
        day=DAY,
    )
    report = validate_dataset.validate(settings.data_dir, "SPY", DAY, settings)
    alignment = next(
        c for c in report.checks if c.name == "observations align with intended poll boundaries"
    )
    assert not alignment.passed


def test_validate_cli(settings: Settings, capsys: object) -> None:
    collect_session(settings, cycles=10)
    code = validate_dataset.main(
        ["--ticker", "SPY", "--date", "2026-09-15", "--data-dir", str(settings.data_dir)]
    )
    assert code == 0


def test_backup_refuses_an_unfinished_day(settings: Settings, tmp_path: Path) -> None:
    collect_session(settings, cycles=3)
    target = backup.LocalBackupTarget(root=tmp_path / "backup")
    result = backup.backup_day(
        settings, DAY, target, data_dir=settings.data_dir, now=OPEN + timedelta(minutes=5)
    )
    assert result.files == []
    assert "not a finished session" in result.skipped[0]


def test_backup_copies_artefacts_with_checksums(settings: Settings, tmp_path: Path) -> None:
    collect_session(settings, cycles=3)
    health_path(settings.data_dir, DAY).parent.mkdir(parents=True, exist_ok=True)
    health_path(settings.data_dir, DAY).write_text(json.dumps({"date": "2026-09-15"}), "utf-8")

    target = backup.LocalBackupTarget(root=tmp_path / "backup")
    after_close = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
    result = backup.backup_day(
        settings, DAY, target, data_dir=settings.data_dir, now=after_close
    )

    stored = {f["relative_path"] for f in result.files}
    assert "SPY/options/2026-09-15.parquet" in stored
    assert "SPY/underlying/2026-09-15.parquet" in stored
    assert "SPY/metadata/2026-09-15_contracts.json" in stored
    assert "health/2026-09-15.json" in stored

    copied = tmp_path / "backup" / "2026-09-15" / "SPY" / "options" / "2026-09-15.parquet"
    assert copied.exists()
    assert (tmp_path / "backup" / "2026-09-15" / "manifest.json").exists()

    checksum = next(f["sha256"] for f in result.files if f["relative_path"].endswith("options/2026-09-15.parquet"))
    assert checksum == backup.sha256_file(option_path(settings.data_dir, "SPY", DAY))
    assert len(checksum) == 64


def test_backup_force_overrides_the_guard(settings: Settings, tmp_path: Path) -> None:
    collect_session(settings, cycles=2)
    target = backup.LocalBackupTarget(root=tmp_path / "backup")
    result = backup.backup_day(
        settings,
        DAY,
        target,
        data_dir=settings.data_dir,
        force=True,
        now=OPEN + timedelta(minutes=5),
    )
    assert result.files


def test_find_complete_days(settings: Settings) -> None:
    collect_session(settings, cycles=2)
    after = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    assert backup.find_complete_days(settings, settings.data_dir, now=after) == [DAY]
    during = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    assert backup.find_complete_days(settings, settings.data_dir, now=during) == []
