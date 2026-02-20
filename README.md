# Hōzō (宝蔵)

**Treasure Storehouse** — A wake-on-demand ZFS backup orchestrator.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## What It Does

Hōzō automates off-site ZFS backups to a sleeping remote machine:

1. **Wake** the remote backup server via Wake-on-LAN
2. **Wait** for SSH to become available
3. **Sync** ZFS datasets using [syncoid](https://github.com/jimsalterjrs/sanoid)
4. **Verify** remote snapshots
5. **Notify** via ntfy.sh, Pushover, or email
6. **Shutdown** the remote server

> Perfect for home-lab users who want off-site backups without running a second NAS 24/7.

---

## Architecture

```
╔══════════════════════════════════════════════════════╗
║           MAIN SERVER (Docker container)             ║
║  hozo orchestrator:                                  ║
║    • Read job config (YAML)                          ║
║    • Send Wake-on-LAN magic packet                   ║
║    • Wait for SSH availability                       ║
║    • Run syncoid (ZFS replication)                   ║
║    • Verify snapshots                                ║
║    • Notify (ntfy / Pushover / email)                ║
║    • SSH: shutdown remote                            ║
║  Web UI: status dashboard, manual trigger            ║
╚══════════════════════════════════════════════════════╝
                     │
      WOL packet + SSH (via Tailscale / VPN)
                     ▼
╔══════════════════════════════════════════════════════╗
║           REMOTE BACKUP BOX (sleeping mini-PC)       ║
║    • Wakes on WOL                                    ║
║    • Tailscale auto-connects                         ║
║    • SSH accepts syncoid connection                  ║
║    • ZFS receives incremental snapshot stream        ║
║    • Optional: backupd agent for health & shutdown   ║
║    • Shuts down when told                            ║
╚══════════════════════════════════════════════════════╝
```

---

## Installation

### From source

```bash
git clone https://github.com/yourusername/hozo
cd hozo
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### With Docker

```bash
# Copy and edit the example config
cp configs/config.example.yaml configs/config.yaml
$EDITOR configs/config.yaml

# Start the container
docker-compose up -d
```

---

## Quick Start

### 1. Create your config

```yaml
# configs/config.yaml
jobs:
  - name: weekly
    source: rpool/data
    target_host: backup-box.tailnet.ts.net
    target_dataset: backup/home-data
    mac_address: "AA:BB:CC:DD:EE:FF"
    schedule: "weekly Sunday 03:00"
    shutdown_after: true
```

### 2. Run a backup now

```bash
hozo --config configs/config.yaml jobs run weekly
```

### 3. Start the web UI

```bash
hozo --config configs/config.yaml serve
# Open http://localhost:8000
```

---

## CLI Reference

```
hozo [--config PATH] [--verbose] COMMAND

Commands:
  jobs list                 List all configured jobs
  jobs run <name>           Run a job immediately
  status [remote] [--job]   SSH into remote and report ZFS health
  wake <name>               Send WOL packet for a job's host
  shutdown <name>           SSH shutdown a job's remote host
  serve [--host] [--port]   Start the web UI and API server
```

**Environment variables:**

| Variable      | Default                       | Description              |
|---------------|-------------------------------|--------------------------|
| `HOZO_CONFIG` | `~/.config/hozo/config.yaml`  | Path to config file      |

---

## Config Schema

```yaml
settings:
  ssh_timeout: 120          # Default SSH wait timeout (seconds)
  ssh_user: root            # Default SSH user
  notifications:
    ntfy_topic: hozo-alerts       # ntfy.sh topic name
    pushover_token: tok_xxx       # Pushover app token
    pushover_user: usr_xxx        # Pushover user key
    smtp:
      host: smtp.example.com
      port: 587
      user: you@example.com
      password: secret
      from_addr: hozo@example.com
      to_addr: admin@example.com
      use_tls: true

jobs:
  - name: string            # Required: unique job identifier
    source: string          # Required: local ZFS dataset (e.g. rpool/data)
    target_host: string     # Required: remote hostname or Tailscale address
    target_dataset: string  # Required: remote ZFS dataset
    mac_address: string     # Required: MAC for WOL (AA:BB:CC:DD:EE:FF)

    # Optional fields (with defaults):
    description: ""         # Human-readable description
    ssh_user: root
    ssh_key: ~/.ssh/id_ed25519
    ssh_port: 22
    recursive: true
    shutdown_after: true
    retries: 3
    retry_delay: 60
    broadcast_ip: 255.255.255.255
    no_privilege_elevation: false
    schedule: ""            # "daily HH:MM" or "weekly <Day> HH:MM"
```

---

## Web API

The web server (started by `hozo serve` or Docker) exposes:

| Method | Path                  | Description                        |
|--------|-----------------------|------------------------------------|
| GET    | `/`                   | HTML dashboard                     |
| GET    | `/status`             | JSON: list of jobs + scheduler state |
| POST   | `/wake`               | Send WOL packet `{"job_name":"…"}` |
| POST   | `/run_backup`         | Start a backup in background       |
| POST   | `/shutdown`           | SSH shutdown remote host           |
| GET    | `/results/{job_name}` | Last result for a job              |
| GET    | `/partials/jobs`      | HTMX partial: job card HTML        |

---

## Remote Agent (backupd)

`backupd` is a lightweight HTTP agent you can optionally run on the remote backup box for richer health reporting and safe shutdown.

**Install on remote box:**

```bash
pip install hozo
backupd  # listens on :9999 by default
```

**Systemd unit:**

```ini
[Unit]
Description=Hōzō backup agent
After=network.target

[Service]
ExecStart=/usr/local/bin/backupd
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Endpoints:**

| Method | Path             | Description               |
|--------|-----------------|---------------------------|
| GET    | `/ping`          | Liveness probe            |
| GET    | `/status`        | ZFS pool status + uptime  |
| POST   | `/shutdown`      | Safe shutdown (exports pools first) |
| GET    | `/disk/{device}` | HDD spin state            |

---

## Development

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

# Full quality pipeline
black src/ tests/ && isort src/ tests/ && flake8 src/ tests/ && mypy src/ && pytest

# Run tests only
pytest -v

# Run with coverage
pytest --cov=hozo --cov=backupd --cov-report=term-missing

# Start dev server
hozo --config configs/config.example.yaml serve
```

---

## Requirements

**Controller (where Hōzō runs):**
- Python 3.10+
- `syncoid` in PATH (from [sanoid](https://github.com/jimsalterjrs/sanoid))
- SSH key access to remote backup box
- Tailscale (or other VPN) for remote access

**Remote backup box:**
- ZFS installed
- SSH enabled and accessible
- Wake-on-LAN enabled in BIOS/UEFI
- Tailscale installed and authenticated

---

## License

MIT — see [LICENSE](LICENSE)
