# Hōzō (宝蔵)

**Treasure Storehouse** — A wake-on-demand ZFS backup orchestrator.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## What It Does

Hōzō automates off-site ZFS backups to a sleeping remote machine:

1. **Wake** the remote backup server via Wake-on-LAN
2. **Wait** for SSH to become available
3. **Spin up** the external USB/SATA drive if it is in standby
4. **Sync** ZFS datasets using [syncoid](https://github.com/jimsalterjrs/sanoid)
5. **Verify** remote snapshots
6. **Notify** via ntfy.sh, Pushover, or email
7. **Shutdown** the remote server

> Built for home-lab users who keep backups on a **tiny NUC or mini-PC** (Intel NUC, Beelink, Minisforum, Raspberry Pi 4/5, etc.) with an external USB or SATA hard drive — power-efficient, silent, and out of the way. The remote machine sleeps when not in use; Hōzō wakes it up, waits for the spinning drive to come online, and shuts it down when finished.

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
║     REMOTE BACKUP BOX  (NUC / mini-PC, sleeping)    ║
║    • Wakes on WOL                                    ║
║    • Tailscale auto-connects                         ║
║    • SSH accepts syncoid connection                  ║
║    • External USB/SATA HDD spun up by Hōzō          ║
║    • ZFS receives incremental snapshot stream        ║
║    • backupd agent: health, drive state, shutdown    ║
║    • Powers down when done                          ║
╚══════════════════════════════════════════════════════╝
          │
   USB / eSATA
          ▼
    ┌───────────┐
    │External   │
    │HDD / SSD  │  (spins down between backups)
    └───────────┘
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

    # Drive spin-up — for NUC/mini-PC targets with USB/SATA standby drives:
    backup_device: /dev/sdb  # block device on the *remote* machine; omit if not needed
    disk_spinup_timeout: 90  # seconds to wait for drive to spin up (default: 90)
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
| GET    | `/disk/{device}`         | Drive state + I/O counters |
| POST   | `/disk/{device}/spinup`  | Kick drive awake, wait up to 60 s |

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

**Remote backup box (NUC / mini-PC recommended):**
- ZFS installed
- SSH enabled and accessible
- Wake-on-LAN enabled in BIOS/UEFI
- Tailscale installed and authenticated
- `hdparm` installed for drive spin-state detection (`apt install hdparm`)
- External USB or SATA hard drive (optional — set `backup_device` in config if present)

---

## Deployment

### Tailscale Serve (recommended)

Tailscale Serve tunnels traffic from your tailnet to a local port with automatic HTTPS and a valid certificate — no port-forwarding, no self-signed certs.

```bash
# Start Hōzō bound to localhost only
hozo serve --host 127.0.0.1 --port 8000

# Expose it on your tailnet over HTTPS
tailscale serve https / proxy http://127.0.0.1:8000
# Accessible at: https://<hostname>.tail<net>.ts.net
```

Then set `auth.rp_id` in `config.yaml` (or via **Settings → WebAuthn RP ID**) to the full Tailscale hostname, e.g. `mymac.tail1234.ts.net`. WebAuthn requires the RP ID to match the hostname in the browser address bar exactly.

### Without Tailscale (UFW / firewall)

```bash
# Bind to a specific LAN interface only
hozo serve --host 192.168.1.10 --port 8000

# UFW — allow only from your home network
ufw allow from 192.168.1.0/24 to any port 8000

# Or restrict by interface (replace eth0 with your interface)
ufw allow in on eth0 to any port 8000
```

Set `auth.rp_id` in config to the hostname you use in the browser (e.g. `192.168.1.10` or `hozo.lan`).
Connections over plain HTTP require `rp_id` to be `localhost`, `127.0.0.1`, or `::1` — for any other hostname you **must** use HTTPS (Tailscale Serve or a reverse proxy with a valid cert).

---

## License

MIT — see [LICENSE](LICENSE)
