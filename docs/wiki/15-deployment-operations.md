# 15 - Deployment and operations

[Index](README.md) | Prev: [14 - Testing](14-testing.md) | Next: [16 - Decision log](16-decision-log.md)

Full instructions: `deployment/README.md`. Release gate: `RELEASE_REVIEW.md`.
This page is the condensed operator view.

## Target

Google Cloud e2-micro (2 vCPU burst, 1 GB RAM), Debian 12, Python 3.12, always
on. Outbound HTTPS only - **no inbound ports**, because the project contains no
web server. Roughly 5-15 MB per ticker-day compressed, so a 30 GB disk holds
years.

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
