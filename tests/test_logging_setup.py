from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from tradier_collector.logging_setup import setup_logging


def test_logging_writes_to_file_and_stdout(tmp_path: Path, capsys: object) -> None:
    root = setup_logging(tmp_path / "logs", level=logging.INFO)
    logging.getLogger("tradier_collector.test").info("hello %s", "world")
    for handler in root.handlers:
        handler.flush()

    log_file = tmp_path / "logs" / "collector.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello world" in content
    assert "INFO" in content
    assert "tradier_collector.test" in content


def test_repeated_setup_does_not_duplicate_handlers(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs")
    first = len(logging.getLogger().handlers)
    setup_logging(tmp_path / "logs")
    assert len(logging.getLogger().handlers) == first


def test_retention_is_configured(tmp_path: Path) -> None:
    root = setup_logging(tmp_path / "logs", retention_days=7)
    rotating = [
        h for h in root.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert rotating
    assert rotating[0].backupCount == 7
    assert rotating[0].when == "MIDNIGHT"
