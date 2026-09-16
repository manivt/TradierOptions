# 03 - Configuration

[Index](README.md) | Prev: [02 - Invariants](02-invariants.md) | Next: [04 - Tradier API client](04-tradier-client.md)

Code: `config.py` | Template: `.env.example` | Tests: `tests/test_config.py`

## Shape

`load_settings(env=None, env_file=".env") -> Settings`.  `Settings` is a frozen
dataclass; every field is validated once, at startup, so a bad deployment fails
immediately instead of halfway through a session.  Passing `env` explicitly is
the testing hook (no environment mutation in tests).

Derived helpers on `Settings`:

* `tz` - `ZoneInfo(market_timezone)`.
* `http_timeout` - `(connect, read)` tuple for `requests`.
* `redacted_summary()` - log-safe dict; the token is always `***redacted***`.
* `__repr__` delegates to `redacted_summary()` so an accidental f-string of the
  settings object cannot leak the token (see I11).

## Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `TRADIER_API_TOKEN` | *required* | `TRADIER_API_KEY` accepted as a legacy alias |
| `TRADIER_ACCOUNT_ID` | empty | unused by the collector; kept for parity with the account |
| `TRADIER_BASE_URL` | `https://api.tradier.com/v1` | must be http(s) |
| `TICKERS` | `SPY,QQQ,IWM` | upper-cased, de-duplicated, order preserved |
| `DISCOVERY_STRIKES_EACH_SIDE` | `10` | 0-200 |
| `QUOTE_BATCH_SIZE` | `50` | 1-500; we do not assume the API maximum |
| `POLL_INTERVAL_SECONDS` | `60` | 1-3600 |
| `DISCOVERY_INTERVAL_CYCLES` | `1` | run full-chain discovery every N cycles |
| `DATA_DIR` / `LOG_DIR` | `./data` / `./logs` | |
| `LOG_RETENTION_DAYS` | `7` | daily rotation |
| `MARKET_TIMEZONE` | `America/New_York` | validated against the IANA database |
| `OPTION_SESSION_OPEN` / `_CLOSE` | `09:30` / `16:15` | close must be later than open |
| `EARLY_CLOSE_OPTION_EXTRA_MINUTES` | `15` | see [05 - Market clock](05-market-clock.md) |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `5` | |
| `HTTP_READ_TIMEOUT_SECONDS` | `20` | |
| `HTTP_MAX_ATTEMPTS` | `3` | bounded retries (I10) |
| `HEALTHY_COVERAGE_PCT` | `98` | must be >= warning |
| `WARNING_COVERAGE_PCT` | `90` | |

## Why a legacy alias exists

The repository already contained a `.env` using `TRADIER_API_KEY` before this
project was written.  Rather than silently failing for the owner, `TOKEN_ENV_VARS`
tries `TRADIER_API_TOKEN` first and falls back.  Removing the alias would break
an existing deployment; keep it.

## Offline fallback in tools

`qa_report.py`, `watchdog.py`, `validate_dataset.py` and `backup.py` all call
`load_settings` inside a `try`, and on `ConfigError` fall back to a synthetic
token (`"qa-offline"` and friends).  Reason: these tools read files only, and
requiring a live API token to inspect an existing dataset would be wrong.  The
collector itself has no such fallback - it needs a real token.

## Failure mode

`ConfigError` (a `ValueError` subclass) with a message naming the variable.
`main.py` prints it to stderr and exits **2**, distinct from a runtime failure
(exit 1) so systemd and operators can tell configuration from crash.

Related: [06 - Scheduler](06-scheduler.md), [15 - Deployment](15-deployment-operations.md)
