# 15 - Deployment and operations

[Index](README.md) | Prev: [14 - Testing](14-testing.md) | Next: [16 - Decision log](16-decision-log.md)

Full instructions: `deployment/README.md`. Release gate: `RELEASE_REVIEW.md`.
This page is the condensed operator view.

## Target

The verified production deployment is an Oracle Cloud Always Free Ampere A1
instance running Oracle Linux on `aarch64`, with 5.5 GiB RAM, 4 GiB swap and a
30 GiB root volume. Python 3.12 is managed by uv. Outbound HTTPS only - **no
inbound ports**, because the project contains no web server. The same unit files
remain portable to another systemd Linux VM, but the Oracle/Linux deployment is
the one that has been exercised against the live API.

## Install shape

```text
/opt/tradier-0dte-collector        __APP_DIR__
  .uv-python/                      uv-managed Python, kept outside $HOME
  .venv/bin/python                 created by uv sync, resolves into .uv-python
  .env                             chmod 600, never in git
  data/  logs/                     the only writable paths in the unit
```

Placeholders in the unit files - `__USER__`, `__GROUP__`, `__APP_DIR__` - are
replaced at install time with `sed`. No production username or path is invented
anywhere in the repository.

## systemd units

| Unit | Role |
| --- | --- |
| `tradier-collector.service` | long-running collector, `Restart=on-failure`, `RestartSec=10`, `KillSignal=SIGTERM`, `TimeoutStopSec=90` |
| `tradier-collector.timer` | optional alternative that starts it at 09:20 ET; use the service **or** the timer, not both |
| `tradier-watchdog.service` | one-shot health check |
| `tradier-watchdog.timer` | every 10 minutes, 09:00-16:50 ET |

Hardening in the service: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
`ProtectHome=true`,
`ReadWritePaths` limited to `data/` and `logs/`,
`RestrictAddressFamilies=AF_INET AF_INET6`.

The deployment guide installs uv's managed Python under `__APP_DIR__/.uv-python`
and builds `.venv` from it. This is required: a default uv installation can make
`.venv/bin/python` resolve through `~/.local/share/uv`, which is intentionally
hidden by `ProtectHome=true` and can also be blocked by SELinux on Oracle Linux.

`TimeoutStopSec=90` exists because a graceful stop finishes the in-flight cycle
and writes the final health file; stopping earlier would leave the day
unfinished and trip the watchdog.

## Runbook

```bash
systemctl status tradier-collector.service
journalctl -u tradier-collector.service -f
journalctl -u tradier-collector.service --since today | grep "cycle "
systemctl restart tradier-collector.service
systemctl list-timers tradier-watchdog.timer
```

For Oracle Linux, confirm that the interpreter does not resolve through the
service user's home directory:

```bash
cd /opt/tradier-0dte-collector
readlink -f .venv/bin/python
.venv/bin/python --version
sudo systemctl is-active tradier-collector.service
```

The resolved Python path must be under `__APP_DIR__/.uv-python/`. If it points
under `~/.local/share/uv`, rebuild the environment using the commands in
`deployment/README.md`; `ProtectHome=true` deliberately hides that location
from the service.

## First live-day evidence (2026-09-17)

The live Tradier smoke test succeeded for SPY, QQQ and IWM. The service ran
under systemd across the session and wrote all expected artefact types:

```text
data/<TICKER>/options/YYYY-MM-DD.parquet
data/<TICKER>/underlying/YYYY-MM-DD.parquet
data/<TICKER>/metadata/YYYY-MM-DD_contracts.json
data/health/YYYY-MM-DD.json
```

End-of-day validation passed the sticky-universe, late out-of-window contract,
timestamp-alignment, duplicate, bid/ask coverage and vendor-Greeks-timestamp
checks. Each ticker had 405 observed of 406 expected minute boundaries
(99.75%). The common missing point was the opening 09:30 ET boundary, caused
by a sub-second late wake-up after the overnight sleep; see [06 - Scheduler](06-scheduler.md).
The day is usable but should be recorded as having one known gap, rather than
described as perfectly complete.

A targeted 11:00 ET SPY read confirmed 48 contracts (24 calls, 24 puts), no
nulls in the core quote/size/volume/open-interest/Greek fields, and a
261-millisecond underlying quote request. The vendor Greeks timestamp was
about 61 minutes older than the poll timestamp; this is preserved provenance,
not a collector failure.

Application logs also land in `logs/collector.log` (7 daily rotating files).
One line per cycle summarises ok/failed/skipped tickers, new contracts, tracked
count, rows saved, batch failures and elapsed time.

## Survival properties

| Event | Why it survives |
| --- | --- |
| SSH disconnect / terminal close | systemd owns the process |
| Python exception | `Restart=on-failure`, `RestartSec=10` |
| VM reboot | `WantedBy=multi-user.target` plus `systemctl enable` |
| Mid-session restart | sticky universe and health counters reload from disk |
| Overnight / weekend / holiday | scheduler sleeps to the next session start |

## Verification before enabling

```bash
sudo -u collector .venv/bin/python scripts/smoke_test_tradier.py
sudo -u collector .venv/bin/python main.py --once
sudo -u collector .venv/bin/python main.py --max-cycles 3
```

The smoke test prints quote availability, the New York date, same-day expiration
availability per ticker, the SPY chain size and strike range, and one sanitized
representative contract. It never prints the token or account id.

## Timezone

The host timezone is irrelevant - the collector resolves New York time through
`zoneinfo`. Keep the VM on UTC and keep NTP enabled; clock skew would shift the
sampling grid.

Related: [06 - Scheduler](06-scheduler.md), [18 - Failure modes](18-failure-modes.md)
