"""Logging configuration: stdout + a timed-rotating file handler.

Seven daily log files are kept by default.  Nothing here ever formats a
secret: the collector passes redacted structures to the logger, and the
Tradier client sanitises response bodies before they reach an exception.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(
    log_dir: Path | str,
    *,
    level: int = logging.INFO,
    filename: str = "collector.log",
    retention_days: int = 7,
    to_stdout: bool = True,
) -> logging.Logger:
    """Configure the root logger and return it.

    Safe to call more than once: existing handlers installed by this function
    are replaced rather than duplicated.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_tradier_collector", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path / filename,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler._tradier_collector = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if to_stdout:
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        stream_handler._tradier_collector = True  # type: ignore[attr-defined]
        root.addHandler(stream_handler)

    # urllib3 is chatty at DEBUG and can echo URLs; keep it at WARNING.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return root
