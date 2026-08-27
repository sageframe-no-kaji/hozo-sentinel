# Hōzō (宝蔵)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Hozo automatically wakes a sleeping backup server,runs ZFS snapshot replication with syncoid,
verifies the snapshots, then shuts the server back down.
Designed for low-power homelab backup nodes.

**Treasure Storehouse** — A wake-on-demand ZFS backup orchestrator.

Hōzō runs **entirely on your controller machine** (the one with the source ZFS pool). It uses [`syncoid`](https://github.com/jimsalterjrs/sanoid) — installed locally — to push ZFS snapshots to a remote backup box over SSH. **No agent is required on the remote machine.** The remote only needs ZFS, SSH, and a user with appropriate ZFS permissions.

**Development Process:** This project was built using the [Ho System](https://atmarcus.net/work/ho-system), a structured methodology for human-AI collaborative development. The human makes every design decision. The AI implements under direction. There is verification at every step.

---

## What It Does

Hōzō automates off-site ZFS backups to a sleeping remote machine:

1. **Wake** the remote backup server via Wake-on-LAN
2. **Wait** for SSH to become available
3. **Spin up** the external USB/SATA drive if it is in standby
4. **Sync** ZFS datasets using [syncoid](https://github.com/jimsalterjrs/sanoid) (with configurable retries)
5. **Verify** remote snapshots
6. **Notify** via ntfy.sh, Pushover, or email
7. **Shutdown** the remote server when done

> Built for home-lab users who keep backups on a **tiny NUC or mini-PC** (Intel NUC, Beelink, Minisforum, Raspberry Pi 4/5, etc.) with an external USB or SATA drive — power-efficient, silent, off when not in use. Hōzō wakes it, waits for the drive to spin up, syncs, shuts it back down.

---

## Architecture

```
╔══════════════════════════════════════════════════════╗
║  YOUR MACHINE  (source / controller)                 ║
║                                                      ║
║  hozo  ←── runs entirely here                        ║
║    • Reads job config (YAML)                         ║
║    • Sends Wake-on-LAN magic packet                  ║
║    • Waits for SSH to come up                        ║
║    • Runs syncoid locally  ←── also installed here   ║
║      syncoid pushes ZFS snapshots over SSH           ║
║    • Verifies remote snapshots                       ║
║    • Notifies (ntfy / Pushover / email)              ║
║    • SSHes in to shut the remote down                ║
║                                                      ║
║  Web UI: dashboard · job log viewer · break-glass    ║
║          restore · settings · WebAuthn auth          ║
╚══════════════════════════════════════════════════════╝
                     │
            WoL + SSH (Tailscale recommended)
                     ▼
╔══════════════════════════════════════════════════════╗
║  REMOTE BACKUP BOX  (NUC / mini-PC, normally off)    ║
║                                                      ║
║  No agent needed — only requires:                    ║
║    • ZFS installed                                   ║
║    • SSH enabled, key-based auth configured          ║
║    • Wake-on-LAN enabled in BIOS/UEFI                ║
║    • (Optional) Tailscale for secure remote access   ║
║    • (Optional) external USB/SATA HDD                ║
║                                                      ║
║  Receives incremental ZFS snapshots via SSH          ║
║  Powers down when backup is complete                 ║
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

## Prerequisites — what you set up vs. what Hōzō does

Hōzō is an **orchestrator, not a provisioner.** It assumes ZFS already exists on both ends and choreographs the replication. Before your first run:

**You provision once, by hand:**

- ZFS installed on both the controller and the backup box.
- The **backup pool created and imported** on the remote box (`zpool create …`, and `zpool import` it again after a reboot). Hōzō never creates or imports pools — vdev topology, `ashift`, compression, and encryption are decisions it deliberately leaves to you.
- SSH key access to the box, with a user that can receive ZFS streams onto the target (`zfs allow … receive,create,mount`, or root).
- Your **source datasets already exist** — that's your live data.

**Hōzō + syncoid handle on every run:**

- The transfer snapshot on the source, the `zfs send | zfs receive` over SSH, and **creating the destination child datasets** under the existing target pool. You don't pre-create `backup/rpool-data`; you *do* need `backup` (the pool) to exist and be imported.
- Waking the box (Wake-on-LAN), waking a spun-down drive (an SSH-issued read), retries, snapshot verification, and optional shutdown.

**Hōzō does NOT manage retention.** The image installs both sanoid and syncoid, but Hōzō only ever calls **syncoid** — it replicates, it never prunes. Snapshot lifecycle on either end (how many you keep, when they expire) is a [sanoid](https://github.com/jimsalterjrs/sanoid) policy you configure yourself. Without one, your backup pool grows until it is full.

**Always-on backup box?** The default flow powers the box down after each run (`shutdown_after: true`). If your box runs 24/7, **uncheck "Shutdown remote host after backup"** on the job (or set `shutdown_after: false`) — otherwise Hōzō powers it off. Drive-only sleep still works: set `backup_device` and Hōzō wakes just that drive over SSH before syncing. Note Hōzō *wakes* drives but does not put them to sleep — configure spindown on the box yourself (`hdparm -S` for SATA, `hd-idle` for USB enclosures).

---

## Setting up an offsite target

The remote box receives data and runs **no Hōzō code**. This is the one-time setup, run **on the target box, as root**.

### 1. Create a dedicated backup pool on the external drive

Identify the drive by its stable `by-id` path — never `/dev/sdX`, which can change across reboots — then create a pool with a name that can't be confused with the box's own pools:

```bash
ls -l /dev/disk/by-id/          # find the new disk
zpool create -o ashift=12 -O compression=lz4 -O atime=off \
  -O canmount=off offsite /dev/disk/by-id/<your-disk-id>
```

`canmount=off` keeps received datasets from auto-mounting — a replication target doesn't need to be mounted, and it sidesteps a non-root mount-permission issue (below).

### 2. Make sure it auto-imports after a reboot

An unattended box must bring the pool back by itself, or every job fails until someone intervenes. Confirm the pool is in the import cache:

```bash
zpool set cachefile=/etc/zfs/zpool.cache offsite
systemctl is-enabled zfs-import-cache.service     # expect: enabled
```

### 3. Authorize the controller and grant receive rights

Add the **controller's** SSH public key to the target, then choose how the controller receives:

```bash
# Simplest — back up as root (job uses ssh_user: root):
#   add the controller's key to /root/.ssh/authorized_keys

# Or delegate to an existing non-root user:
zfs allow <user> create,receive,mount,mountpoint offsite
```

Verify from the controller (also confirms zfs is on the non-interactive SSH PATH):

```bash
ssh <target> 'command -v zfs zpool && zpool status offsite'
```

### 4. Seed the first backup locally, then relocate the drive

`syncoid` has a **1-hour per-run timeout**, so the large initial replication should not run over the internet. Seed it at LAN speed, then physically move the drive:

```bash
# On the controller, with the drive attached locally and the pool imported:
hozo jobs run <job>          # full initial replication over LAN

zpool export offsite         # flush and detach cleanly
#  → carry the drive to the offsite box →
zpool import offsite         # on the target box
```

Then point the job's `target_host` at the offsite box. Subsequent runs send only small incrementals (matched by snapshot GUID), which finish well under the timeout.

> **Spindown on an unattended drive:** prefer leaving it spinning. A USB bridge that drops off the bus during standby can fault the pool with no one on-site to recover it — reliability of the offsite copy beats drive longevity. See [Prerequisites](#prerequisites--what-you-set-up-vs-what-hōzō-does).

---

## Installation

```bash
git clone https://github.com/sageframe-no-kaji/hozo-sentinel
cd hozo-sentinel
python -m venv venv
source venv/bin/activate
pip install -e .
```

`syncoid` must also be installed and on PATH. It ships with [sanoid](https://github.com/jimsalterjrs/sanoid):

```bash
# Debian / Ubuntu
sudo apt install sanoid

# Or install from source
git clone https://github.com/jimsalterjrs/sanoid
sudo cp sanoid/syncoid /usr/local/bin/
```

---

## Quick Start

### 1. Bootstrap the web UI

On first run with no config file, Hōzō starts in **bootstrap mode** and guides you through initial setup in the browser:

```bash
hozo serve
# Open http://localhost:8000
# → Register a WebAuthn passkey
# → Configure jobs and settings in the UI
# → Config is written to ~/.config/hozo/config.yaml
```

### 2. Or write a config directly

```yaml
# ~/.config/hozo/config.yaml
settings:
  ssh_timeout: 120
  ssh_user: root

auth:
  rp_id: localhost
  rp_name: Hōzō

jobs:
  - name: weekly
    source: rpool/data
    target_host: backup-box.tailnet.ts.net
    target_dataset: backup/rpool-data
    mac_address: "AA:BB:CC:DD:EE:FF"
    schedule: "weekly Sunday 03:00"
    shutdown_after: true      # set false if the box runs 24/7
```

### 3. Run a backup now

```bash
hozo jobs run weekly
```

### 4. Start the web UI

```bash
hozo serve
# Open http://localhost:8000
```

---

## CLI Reference

```
hozo [--config PATH] [--verbose] COMMAND

Commands:
  jobs list                 List all configured jobs
  jobs run <name>           Run a job immediately (foreground, full output)
  wake <name>               Send WOL packet for a job's host
  shutdown <name>           SSH shutdown a job's remote host
  serve [--host] [--port]   Start the web UI + API server
```

**Defaults:**

| Variable      | Default                      | Description         |
|---------------|------------------------------|---------------------|
| `HOZO_CONFIG` | `~/.config/hozo/config.yaml` | Path to config file |
| `--host`      | `127.0.0.1`                  | Bind address        |
| `--port`      | `8000`                       | Listen port         |

---

## Config Reference

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

auth:
  rp_id: mymac.tail1234.ts.net   # Must match the hostname in the browser
  rp_name: Hōzō
  session_secret: <random>        # Auto-generated on first run
  credentials: []                 # WebAuthn passkeys (managed by the UI)

jobs:
  - name: string            # Required: unique job identifier
    source: string          # Required: local ZFS dataset  (e.g. rpool/data)
    target_host: string     # Required: remote hostname or Tailscale address
    target_dataset: string  # Required: remote ZFS dataset (e.g. backup/rpool-data)
    mac_address: string     # Required: MAC for WOL (AA:BB:CC:DD:EE:FF)

    # Optional (all have defaults):
    description: ""
    ssh_user: root
    ssh_key: ~/.ssh/id_ed25519
    ssh_port: 22
    recursive: true
    shutdown_after: true     # power the box off after backup; false for always-on boxes
    retries: 3
    retry_delay: 60         # seconds between retry attempts
    wol_broadcast: 255.255.255.255
    no_privilege_elevation: false
    schedule: ""            # "daily HH:MM"  or  "weekly <Day> HH:MM"

    # Drive spin-up (for NUC/mini-PC targets with USB/SATA standby drives):
    backup_device: /dev/sdb  # block device on the *remote* machine
    disk_spinup_timeout: 90  # seconds to wait for the drive to spin up
```

---

## Web UI

Start with `hozo serve` and open `http://localhost:8000` (or your Tailscale hostname).

### Dashboard

Shows every configured job with its last run status, duration, snapshot count, and controls:

- **▶ Run** — trigger an immediate backup in the background
- **✏ Edit** — edit job config in the browser
- **📋 Log** — open the per-job log viewer

### Job Log Viewer  (`/jobs/{name}/log`)

Full captured output from the last run, colour-coded:

- Red → `ERROR`
- Yellow → `WARNING`
- Cyan → `[syncoid]` output lines
- Grey → informational

When a job is still running the page polls every 3 s and updates live.

### Break-glass Restore  (`/jobs/{name}/restore`)

Accessible only from the **very bottom of the log page** — not in the nav, not on the dashboard. Pulls the remote backup back onto the local machine using syncoid in reverse.

**What it does:**
- Runs syncoid with source and destination swapped (`remote:backup → local:source`)
- Passes `--force-delete` — local snapshots absent on the remote are destroyed
- Single-attempt, no retries, no scheduler involvement
- Requires typing the exact job name to confirm before anything runs

**Use this only for disaster recovery.** There is no undo.

### Settings  (`/settings`)

Edit global settings (SSH timeout, notifications) and WebAuthn RP ID in the browser. Changes are written back to the config file.

### Registered Devices  (`/auth/devices`)

List and revoke registered WebAuthn passkeys.

---

## Web API

All HTML routes require a valid session cookie (WebAuthn login). The JSON endpoints are listed below.

| Method | Path                             | Description                            |
|--------|----------------------------------|----------------------------------------|
| GET    | `/`                              | HTML dashboard                         |
| GET    | `/status`                        | JSON: jobs + scheduler state           |
| POST   | `/wake`                          | Send WOL packet `{"job_name":"…"}`     |
| POST   | `/run_backup`                    | Start backup in background             |
| POST   | `/shutdown`                      | SSH shutdown remote host               |
| GET    | `/results/{job_name}`            | JSON: last result for a job            |
| GET    | `/jobs/{name}/log`               | HTML: per-job log viewer               |
| GET    | `/jobs/{name}/log/lines`         | HTMX partial: log lines only           |
| GET    | `/jobs/{name}/restore`           | HTML: break-glass restore confirm      |
| POST   | `/jobs/{name}/restore`           | Execute restore (typed confirmation)   |
| GET    | `/jobs/{name}/restore/log`       | HTML: restore log viewer               |
| GET    | `/jobs/{name}/restore/log/lines` | HTMX partial: restore log lines        |
| GET    | `/settings`                      | HTML: settings editor                  |
| POST   | `/settings`                      | Save settings                          |
| GET    | `/jobs/{name}/edit`              | HTML: job editor                       |
| POST   | `/jobs/{name}/edit`              | Save job config                        |
| GET    | `/auth/login`                    | HTML: WebAuthn login                   |
| POST   | `/auth/login/begin`              | WebAuthn assertion challenge           |
| POST   | `/auth/login/complete`           | WebAuthn assertion verify + set cookie |
| POST   | `/auth/logout`                   | Clear session cookie                   |
| GET    | `/auth/register`                 | HTML: passkey registration             |
| POST   | `/auth/register/begin`           | WebAuthn registration challenge        |
| POST   | `/auth/register/complete`        | WebAuthn registration save             |
| GET    | `/auth/devices`                  | HTML: registered devices list          |
| POST   | `/auth/devices/{id}/delete`      | Revoke a passkey                       |

---

## Deployment

Set `HOZO_SESSION_SECRET` from a `.env` file (see [`.env.example`](.env.example)) so the session secret never gets written into `config.yaml`.

### Tailscale Serve (recommended)

Tailscale Serve tunnels traffic from your tailnet to a local port with automatic HTTPS and a valid certificate — no port-forwarding, no self-signed certs.

```bash
# Start Hōzō bound to localhost only
hozo serve --host 127.0.0.1 --port 8000

# Expose on your tailnet over HTTPS
tailscale serve https / proxy http://127.0.0.1:8000
# Accessible at: https://<hostname>.tail<net>.ts.net
```

Set `auth.rp_id` in your config (or via **Settings → WebAuthn RP ID** in the UI) to the full Tailscale hostname, e.g. `mymac.tail1234.ts.net`. WebAuthn requires the RP ID to match the hostname in the browser address bar exactly.

### Without Tailscale (LAN only)

```bash
# Bind to a specific LAN interface
hozo serve --host 192.168.1.10 --port 8000
```

Set `auth.rp_id` to the hostname you use in the browser (e.g. `192.168.1.10` or `hozo.lan`).

> Connections over plain HTTP require `rp_id` to be `localhost`, `127.0.0.1`, or `::1`. For any other hostname you **must** use HTTPS.

### Docker: the config-file mount is intentionally narrow

`docker-compose.yml` bind-mounts `/opt/services/hozo/configs/config.yaml` directly — the *file*, not its parent directory. The container can read and write that single file (the Settings page needs to persist changes, the bootstrap path needs to seed `auth.session_secret`) but cannot see or touch sibling files in `/opt/services/hozo/configs/`. If you keep operator backups (`config.yaml.bak`, exported snapshots, etc.) in that directory, they remain invisible to the container — so a compromised web tier cannot exfiltrate them or write a malicious passkey credential anywhere outside `config.yaml` itself.

Residual risk: a compromised web tier can still append a passkey credential to `config.yaml` and lock the operator out. Mitigations:
- Keep `config.yaml` under host-side version control or snapshot it (e.g., Sanoid on the ZFS dataset) so you can roll back.
- Treat `config.yaml` as a sensitive file (`chmod 600`, owner-only).

### Non-standard ports

Hōzō computes the expected passkey origin as `https://<rp_id>` by default. If you serve the UI on a non-standard port (Caddy on `:8443`, an internal reverse-proxy on `:8080`, etc.) the browser will send the full `https://host:port` URL and registration/login will fail with an opaque "origin mismatch" error.

Override by setting `auth.origin` in `config.yaml`:

```yaml
auth:
  rp_id: hozo.tailnet.ts.net
  origin: https://hozo.tailnet.ts.net:8443
```

---

## Requirements

**Controller (where Hōzō runs):**
- Python 3.10+
- `syncoid` in PATH (from [sanoid](https://github.com/jimsalterjrs/sanoid))
- SSH key access to the remote backup box

**Remote backup box:**
- ZFS installed and configured
- SSH enabled, key-based auth set up
- Wake-on-LAN enabled in BIOS/UEFI
- `hdparm` if using `backup_device` spin-up detection (`apt install hdparm`)
- (Optional) Tailscale for secure access over the internet

---

## Development

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Full quality pipeline
venv/bin/flake8 src/ tests/ \
  && venv/bin/mypy src/ --ignore-missing-imports \
  && venv/bin/pytest --tb=short -q

# Tests only
venv/bin/pytest -v

# With coverage
venv/bin/pytest --cov=hozo --cov-report=term-missing

# Dev server (bootstrap mode — no config needed)
venv/bin/hozo serve
```

Tests live in [`tests/`](tests/) and cover backup logic, job orchestration, config loading, scheduling, SSH helpers, WoL, WebAuthn, the `backupd` agent, and the API routes. Current test count: **336 tests**.

---

## License

MIT — see [LICENSE](LICENSE)

---

Made by Andrew T. Marcus following the [Ho Process](https://github.com/sageframe-no-kaji/ho-system) · [github.com/sageframe-no-kaji](https://github.com/sageframe-no-kaji)
