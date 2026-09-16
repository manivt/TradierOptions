"""Typed configuration for the Tradier 0DTE collector.

All settings come from environment variables (optionally seeded from a ``.env``
file).  Validation happens once, at startup, so a misconfigured deployment
fails immediately and loudly instead of half way through a trading session.

Secrets are never logged: :meth:`Settings.redacted_summary` deliberately omits
the API token, and ``repr`` of :class:`Settings` is customised for the same
reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.tradier.com/v1"
DEFAULT_TICKERS = ("SPY", "QQQ", "IWM")

#: Primary env var name.  ``TRADIER_API_KEY`` is accepted as a legacy alias so
#: that pre-existing ``.env`` files keep working.
TOKEN_ENV_VARS = ("TRADIER_API_TOKEN", "TRADIER_API_KEY")


class ConfigError(ValueError):
    """Raised when the environment does not describe a usable configuration."""


def _get(env: dict[str, str], key: str, default: str) -> str:
    raw = env.get(key)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _get_int(env: dict[str, str], key: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _get(env, key, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {value}")
    return value


def _get_float(
    env: dict[str, str], key: str, default: float, *, minimum: float, maximum: float
) -> float:
    raw = _get(env, key, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {value}")
    return value


def _get_time(env: dict[str, str], key: str, default: str) -> dt_time:
    raw = _get(env, key, default)
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ConfigError(f"{key} must look like HH:MM, got {raw!r}")
    try:
        numbers = [int(p) for p in parts]
    except ValueError as exc:
        raise ConfigError(f"{key} must look like HH:MM, got {raw!r}") from exc
    hour, minute = numbers[0], numbers[1]
    second = numbers[2] if len(numbers) == 3 else 0
    try:
        return dt_time(hour, minute, second)
    except ValueError as exc:
        raise ConfigError(f"{key} is not a valid clock time: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Fully-validated collector configuration."""

    api_token: str = field(repr=False)
    account_id: str = field(default="", repr=False)
    base_url: str = DEFAULT_BASE_URL

    tickers: tuple[str, ...] = DEFAULT_TICKERS
    discovery_strikes_each_side: int = 10
    quote_batch_size: int = 50
    poll_interval_seconds: int = 60
    #: Run full-chain discovery every N cycles (1 == every cycle).
    discovery_interval_cycles: int = 1

    data_dir: Path = Path("./data")
    log_dir: Path = Path("./logs")
    log_retention_days: int = 7

    market_timezone: str = "America/New_York"
    option_session_open: dt_time = dt_time(9, 30)
    option_session_close: dt_time = dt_time(16, 15)
    #: Minutes after the *equity* close during which ETF options keep trading.
    #: Applied to early-close sessions; see ``tradier_collector/market_clock.py``.
    early_close_option_extra_minutes: int = 15

    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 20.0
    http_max_attempts: int = 3

    healthy_coverage_pct: float = 98.0
    warning_coverage_pct: float = 90.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.market_timezone)

    @property
    def http_timeout(self) -> tuple[float, float]:
        return (self.http_connect_timeout_seconds, self.http_read_timeout_seconds)

    def redacted_summary(self) -> dict[str, object]:
        """A log-safe view of the configuration (never includes the token)."""
        return {
            "base_url": self.base_url,
            "tickers": list(self.tickers),
            "discovery_strikes_each_side": self.discovery_strikes_each_side,
            "quote_batch_size": self.quote_batch_size,
            "poll_interval_seconds": self.poll_interval_seconds,
            "discovery_interval_cycles": self.discovery_interval_cycles,
            "data_dir": str(self.data_dir),
            "log_dir": str(self.log_dir),
            "market_timezone": self.market_timezone,
            "option_session_open": self.option_session_open.strftime("%H:%M"),
            "option_session_close": self.option_session_close.strftime("%H:%M"),
            "early_close_option_extra_minutes": self.early_close_option_extra_minutes,
            "http_timeout": list(self.http_timeout),
            "healthy_coverage_pct": self.healthy_coverage_pct,
            "warning_coverage_pct": self.warning_coverage_pct,
            "api_token": "***redacted***",
            "account_id_configured": bool(self.account_id),
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Settings({self.redacted_summary()})"


def load_settings(
    env: dict[str, str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = ".env",
) -> Settings:
    """Build and validate :class:`Settings`.

    ``env`` is primarily a testing hook; when omitted the process environment
    (optionally seeded from ``env_file``) is used.
    """
    if env is None:
        if env_file is not None and Path(env_file).exists():
            load_dotenv(env_file, override=False)
        env = dict(os.environ)

    token = ""
    for name in TOKEN_ENV_VARS:
        token = _get(env, name, "").strip()
        if token:
            break
    if not token:
        raise ConfigError(
            "TRADIER_API_TOKEN is required (TRADIER_API_KEY accepted as an alias). "
            "Copy .env.example to .env and fill it in."
        )

    base_url = _get(env, "TRADIER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"TRADIER_BASE_URL must be an http(s) URL, got {base_url!r}")

    raw_tickers = _get(env, "TICKERS", ",".join(DEFAULT_TICKERS))
    tickers = tuple(dict.fromkeys(t.strip().upper() for t in raw_tickers.split(",") if t.strip()))
    if not tickers:
        raise ConfigError("TICKERS must contain at least one symbol")

    timezone = _get(env, "MARKET_TIMEZONE", "America/New_York")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"MARKET_TIMEZONE is not a known IANA zone: {timezone!r}") from exc

    session_open = _get_time(env, "OPTION_SESSION_OPEN", "09:30")
    session_close = _get_time(env, "OPTION_SESSION_CLOSE", "16:15")
    if session_close <= session_open:
        raise ConfigError("OPTION_SESSION_CLOSE must be later than OPTION_SESSION_OPEN")

    healthy = _get_float(env, "HEALTHY_COVERAGE_PCT", 98.0, minimum=0.0, maximum=100.0)
    warning = _get_float(env, "WARNING_COVERAGE_PCT", 90.0, minimum=0.0, maximum=100.0)
    if warning > healthy:
        raise ConfigError("WARNING_COVERAGE_PCT must be <= HEALTHY_COVERAGE_PCT")

    return Settings(
        api_token=token,
        account_id=_get(env, "TRADIER_ACCOUNT_ID", ""),
        base_url=base_url,
        tickers=tickers,
        discovery_strikes_each_side=_get_int(
            env, "DISCOVERY_STRIKES_EACH_SIDE", 10, minimum=0, maximum=200
        ),
        quote_batch_size=_get_int(env, "QUOTE_BATCH_SIZE", 50, minimum=1, maximum=500),
        poll_interval_seconds=_get_int(env, "POLL_INTERVAL_SECONDS", 60, minimum=1, maximum=3600),
        discovery_interval_cycles=_get_int(
            env, "DISCOVERY_INTERVAL_CYCLES", 1, minimum=1, maximum=1440
        ),
        data_dir=Path(_get(env, "DATA_DIR", "./data")),
        log_dir=Path(_get(env, "LOG_DIR", "./logs")),
        log_retention_days=_get_int(env, "LOG_RETENTION_DAYS", 7, minimum=1, maximum=365),
        market_timezone=timezone,
        option_session_open=session_open,
        option_session_close=session_close,
        early_close_option_extra_minutes=_get_int(
            env, "EARLY_CLOSE_OPTION_EXTRA_MINUTES", 15, minimum=0, maximum=120
        ),
        http_connect_timeout_seconds=_get_float(
            env, "HTTP_CONNECT_TIMEOUT_SECONDS", 5.0, minimum=0.1, maximum=120.0
        ),
        http_read_timeout_seconds=_get_float(
            env, "HTTP_READ_TIMEOUT_SECONDS", 20.0, minimum=0.1, maximum=300.0
        ),
        http_max_attempts=_get_int(env, "HTTP_MAX_ATTEMPTS", 3, minimum=1, maximum=10),
        healthy_coverage_pct=healthy,
        warning_coverage_pct=warning,
    )
