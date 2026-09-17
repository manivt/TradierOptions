# Deployment (Google Cloud e2-micro, systemd)

Placeholders used in the unit files - replace all three before installing:

```text
__USER__     non-root service account        e.g. collector
__GROUP__    its primary group               e.g. collector
__APP_DIR__  checkout directory              e.g. /opt/tradier-0dte-collector
```

## 1. Create the VM

```bash
gcloud compute instances create tradier-collector \
    --machine-type=e2-micro \
    --zone=us-east4-b \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB \
    --no-address-if-you-use-cloud-nat   # optional; no inbound ports are needed
```

The collector makes only outbound HTTPS requests.  Do **not** open inbound
80/443: there is no web server in this project.

A us-east region keeps latency to the Tradier API low.  30 GB of standard disk
is far more than a year of this dataset needs (roughly 5-15 MB per ticker-day
compressed).

## 2. Prepare the host

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git

sudo useradd --system --create-home --shell /usr/sbin/nologin collector
sudo mkdir -p /opt/tradier-0dte-collector
sudo chown collector:collector /opt/tradier-0dte-collector
```

## 3. Install the application

```bash
sudo -u collector git clone <your-repo> /opt/tradier-0dte-collector
cd /opt/tradier-0dte-collector

# uv is the project standard.  Keep its managed Python below the application
# directory: systemd hides the service user's home directory for hardening.
sudo -u collector curl -LsSf https://astral.sh/uv/install.sh | sudo -u collector sh
sudo -u collector mkdir -p /opt/tradier-0dte-collector/.uv-python
sudo -u collector env UV_PYTHON_INSTALL_DIR=/opt/tradier-0dte-collector/.uv-python \
    /home/collector/.local/bin/uv python install \
    --install-dir /opt/tradier-0dte-collector/.uv-python 3.12
sudo -u collector env UV_PYTHON_INSTALL_DIR=/opt/tradier-0dte-collector/.uv-python \
    /home/collector/.local/bin/uv sync --frozen

sudo -u collector cp .env.example .env
sudo -u collector nano .env          # paste the Tradier token
sudo chmod 600 .env
```

`uv sync` creates `.venv` inside the checkout, and its interpreter resolves
under `__APP_DIR__/.uv-python/` rather than `~/.local`. This is required by
the hardened systemd unit (`ProtectHome=true`).

### Oracle Cloud Ampere A1 / Oracle Linux

The same service units work on ARM64. Use the existing non-root `opc` user and
the Oracle Linux package manager; substitute `opc` for `collector` in the
commands above:

```bash
sudo dnf install -y git curl
sudo mkdir -p /opt/tradier-0dte-collector
sudo chown opc:opc /opt/tradier-0dte-collector
sudo -u opc git clone <your-repo> /opt/tradier-0dte-collector
cd /opt/tradier-0dte-collector

sudo -u opc curl -LsSf https://astral.sh/uv/install.sh | sudo -u opc sh
sudo -u opc mkdir -p /opt/tradier-0dte-collector/.uv-python
sudo -u opc env UV_PYTHON_INSTALL_DIR=/opt/tradier-0dte-collector/.uv-python \
    /home/opc/.local/bin/uv python install \
    --install-dir /opt/tradier-0dte-collector/.uv-python 3.12
sudo -u opc env UV_PYTHON_INSTALL_DIR=/opt/tradier-0dte-collector/.uv-python \
    /home/opc/.local/bin/uv sync --frozen
```

For the unit placeholders, use `__USER__=opc`, `__GROUP__=opc`, and
`__APP_DIR__=/opt/tradier-0dte-collector`. No inbound ports other than a
restricted SSH rule are needed.

## 4. Verify before enabling the service

```bash
sudo -u collector .venv/bin/python scripts/smoke_test_tradier.py
sudo -u collector .venv/bin/python main.py --once
sudo -u collector .venv/bin/python main.py --max-cycles 3
```

## 5. Install the units

```bash
sudo cp deployment/tradier-collector.service /etc/systemd/system/
sudo cp deployment/tradier-watchdog.service /etc/systemd/system/
sudo cp deployment/tradier-watchdog.timer /etc/systemd/system/
sudo sed -i 's|__USER__|collector|g;s|__GROUP__|collector|g;s|__APP_DIR__|/opt/tradier-0dte-collector|g' \
    /etc/systemd/system/tradier-collector.service \
    /etc/systemd/system/tradier-watchdog.service

sudo systemctl daemon-reload
sudo systemctl enable --now tradier-collector.service
sudo systemctl enable --now tradier-watchdog.timer
```

`tradier-collector.timer` is optional and mutually exclusive with running the
service continuously; the service manages its own session window, so the
simplest correct setup is the always-on service.

## 6. Operate

```bash
systemctl status tradier-collector.service
journalctl -u tradier-collector.service -f
journalctl -u tradier-collector.service --since today
systemctl list-timers tradier-watchdog.timer
systemctl restart tradier-collector.service
systemctl stop tradier-collector.service      # SIGTERM: finishes the cycle
```

Application logs also land in `__APP_DIR__/logs/collector.log` (7 daily files).

## Survival properties

* SSH disconnect / terminal close: systemd owns the process, not the shell.
* Python exception: `Restart=on-failure` with `RestartSec=10`.
* VM reboot: `WantedBy=multi-user.target` plus `systemctl enable`.
* Restart mid-session: the sticky universe and the health counters are both
  reloaded from disk, so the day continues rather than restarting.

## Timezone

Unit files use `America/New_York` in `OnCalendar` (systemd >= 250).  The
collector itself does not depend on the host timezone: it resolves New York
time internally through `zoneinfo`.  Keeping the VM on UTC is fine.
