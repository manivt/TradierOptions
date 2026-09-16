from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from config import ConfigError, load_settings


def test_defaults_are_applied() -> None:
    settings = load_settings({"TRADIER_API_TOKEN": "abc"}, env_file=None)
    assert settings.tickers == ("SPY", "QQQ", "IWM")
    assert settings.discovery_strikes_each_side == 10
    assert settings.quote_batch_size == 50
    assert settings.poll_interval_seconds == 60
    assert settings.data_dir == Path("./data")
    assert settings.option_session_open == time(9, 30)
    assert settings.option_session_close == time(16, 15)
    assert settings.healthy_coverage_pct == 98.0
    assert settings.warning_coverage_pct == 90.0
    assert settings.http_timeout == (5.0, 20.0)


def test_missing_token_is_rejected() -> None:
    with pytest.raises(ConfigError, match="TRADIER_API_TOKEN"):
        load_settings({}, env_file=None)


def test_legacy_token_alias_is_accepted() -> None:
    settings = load_settings({"TRADIER_API_KEY": "legacy"}, env_file=None)
    assert settings.api_token == "legacy"


def test_blank_token_is_rejected() -> None:
    with pytest.raises(ConfigError):
        load_settings({"TRADIER_API_TOKEN": "   "}, env_file=None)


@pytest.mark.parametrize(
    "env",
    [
        {"QUOTE_BATCH_SIZE": "0"},
        {"QUOTE_BATCH_SIZE": "not-a-number"},
        {"POLL_INTERVAL_SECONDS": "-5"},
        {"DISCOVERY_STRIKES_EACH_SIDE": "9999"},
        {"MARKET_TIMEZONE": "Mars/Olympus"},
        {"TRADIER_BASE_URL": "ftp://example.com"},
        {"OPTION_SESSION_OPEN": "9-30"},
        {"OPTION_SESSION_OPEN": "25:00"},
        {"OPTION_SESSION_CLOSE": "09:00"},
        {"HEALTHY_COVERAGE_PCT": "80", "WARNING_COVERAGE_PCT": "95"},
        {"TICKERS": " , "},
    ],
)
def test_invalid_values_are_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_settings({"TRADIER_API_TOKEN": "abc", **env}, env_file=None)


def test_tickers_are_normalised_and_deduplicated() -> None:
    settings = load_settings(
        {"TRADIER_API_TOKEN": "abc", "TICKERS": "spy, qqq ,SPY"}, env_file=None
    )
    assert settings.tickers == ("SPY", "QQQ")


def test_secrets_are_never_rendered() -> None:
    settings = load_settings({"TRADIER_API_TOKEN": "super-secret"}, env_file=None)
    rendered = f"{settings!r} {settings.redacted_summary()}"
    assert "super-secret" not in rendered
    assert "***redacted***" in rendered
