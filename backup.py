"""Backup of completed trading days with SHA-256 checksums.

The current day is never backed up while it is still being written unless
``--force`` is passed, because a Parquet file copied mid-rewrite could be
captured in an inconsistent state.  (The collector itself writes atomically,
so the *production* file is always valid; the restriction is about copying a
day that is not finished yet, which would produce a partial backup that looks
complete.)

Targets are pluggable: :class:`LocalBackupTarget` writes into ``backup/``.
Adding Google Cloud Storage later means implementing ``BackupTarget.store``
and nothing else.

    python backup.py --date 2026-09-15
    python backup.py --all-complete-days
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from config import ConfigError, Settings, load_settings
from tradier_collector.market_clock import build_market_clock
from tradier_collector.storage import (
    health_path,
    metadata_path,
    option_path,
    underlying_path,
)

logger = logging.getLogger(__name__)
CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupTarget(Protocol):
    """Where backed-up files go.  Implement this for cloud storage later."""

    def store(self, source: Path, relative_path: str) -> str: ...

    def describe(self) -> str: ...


@dataclass
class LocalBackupTarget:
    """Copies files into a local backup directory."""

    root: Path

    def store(self, source: Path, relative_path: str) -> str:
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return str(destination)

    def describe(self) -> str:
        return f"local directory {self.root}"


@dataclass
class BackupResult:
    trading_date: str
    files: list[dict[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.trading_date}: {len(self.files)} files backed up"]
        lines.extend(f"  + {f['relative_path']}  {f['sha256'][:16]}..." for f in self.files)
        lines.extend(f"  - skipped {s}" for s in self.skipped)
        return "\n".join(lines)


def day_is_complete(settings: Settings, day: date, now: datetime | None = None) -> bool:
    """True when the session for ``day`` has finished (safe to back up)."""
    now = now or datetime.now(tz=UTC)
    clock = build_market_clock(settings)
    window = clock.session_window(day)
    if window is None:
        return False
    return now > window.end_utc


def backup_day(
    settings: Settings,
    day: date,
    target: BackupTarget,
    *,
    data_dir: Path | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> BackupResult:
    """Back up every artefact for one trading date."""
    root = Path(data_dir or settings.data_dir)
    result = BackupResult(trading_date=day.isoformat())

    if not force and not day_is_complete(settings, day, now):
        result.skipped.append(
            f"{day.isoformat()} is not a finished session; refusing to copy files that "
            f"are still being written (use --force to override)"
        )
        return result

    candidates: list[tuple[Path, str]] = []
    for ticker in settings.tickers:
        candidates.append((option_path(root, ticker, day), f"{ticker}/options/{day}.parquet"))
        candidates.append(
            (underlying_path(root, ticker, day), f"{ticker}/underlying/{day}.parquet")
        )
        candidates.append(
            (metadata_path(root, ticker, day), f"{ticker}/metadata/{day}_contracts.json")
        )
    candidates.append((health_path(root, day), f"health/{day}.json"))

    for source, relative in candidates:
        if not source.exists():
            result.skipped.append(f"missing {relative}")
            continue
        checksum = sha256_file(source)
        stored = target.store(source, f"{day.isoformat()}/{relative}")
        result.files.append(
            {
                "relative_path": relative,
                "source": str(source),
                "stored_at": stored,
                "sha256": checksum,
                "bytes": str(source.stat().st_size),
            }
        )

    if result.files:
        manifest: dict[str, object] = {
            "trading_date": day.isoformat(),
            "created_utc": datetime.now(tz=UTC).isoformat(),
            "target": target.describe(),
            "files": result.files,
        }
        manifest_path = Path(
            target.store(
                _write_manifest(root, day, manifest), f"{day.isoformat()}/manifest.json"
            )
        )
        logger.info("Backup manifest stored at %s", manifest_path)
    return result


def _write_manifest(root: Path, day: date, manifest: dict[str, object]) -> Path:
    """Write the manifest next to the data, then let the target store it too."""
    path = root / "health" / f"{day.isoformat()}_backup_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def find_complete_days(
    settings: Settings, data_dir: Path, now: datetime | None = None
) -> list[date]:
    """Every finished session that has at least one artefact on disk."""
    now = now or datetime.now(tz=UTC)
    days: set[date] = set()
    for ticker in settings.tickers:
        for sub in ("options", "underlying"):
            directory = data_dir / ticker / sub
            if not directory.exists():
                continue
            for path in directory.glob("*.parquet"):
                try:
                    days.add(datetime.strptime(path.stem, "%Y-%m-%d").date())
                except ValueError:
                    logger.warning("Ignoring unexpected file name %s", path)
    return sorted(day for day in days if day_is_complete(settings, day, now))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Back up collected trading days")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="Trading date to back up (YYYY-MM-DD)")
    group.add_argument("--all-complete-days", action="store_true")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--backup-dir", default="./backup")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Back up even an unfinished session (may capture a partial day)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigError:
        settings = load_settings({"TRADIER_API_TOKEN": "backup-offline"}, env_file=None)

    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir
    target = LocalBackupTarget(root=Path(args.backup_dir))

    days = (
        find_complete_days(settings, data_dir)
        if args.all_complete_days
        else [datetime.strptime(args.date, "%Y-%m-%d").date()]
    )
    if not days:
        print("No complete days to back up.")
        return 0

    backed_up = 0
    for day in days:
        result = backup_day(settings, day, target, data_dir=data_dir, force=args.force)
        print(result.render())
        backed_up += len(result.files)
    print(f"\nBacked up {backed_up} files to {target.describe()}")
    return 0 if backed_up else 1


if __name__ == "__main__":
    sys.exit(main())
