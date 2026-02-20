# Hōzō — Architecture

## System Overview

```
╔══════════════════════════════════════════════════════════════╗
║                MAIN SERVER (Home / Office)                   ║
║  - Proxmox node, Linux server, or any always-on machine      ║
║  - Docker container: "hozo" orchestrator                     ║
║  - Holds job definitions, SSH keys, schedules                ║
║  - Sends Wake-on-LAN magic packets (UDP broadcast)           ║
║  - Runs syncoid over encrypted VPN tunnel                    ║
║  - Verifies replication & reports status                     ║
║  - Web UI at :8000 (HTMX dashboard + JSON API)               ║
╚══════════════════════════════════════════════════════════════╝
               │
               │  1. UDP WOL magic packet (LAN broadcast or Tailscale)
               │  2. TCP :22 SSH (syncoid ZFS send/receive)
               │  3. Optional: HTTP :9999 (backupd agent)
               │
               ▼  (Tailscale / WireGuard overlay)
╔══════════════════════════════════════════════════════════════╗
║                REMOTE BACKUP MINI-SERVER                     ║
║  - Micro PC / Mini PC / Raspberry Pi with ZFS                ║
║  - Wakes via WOL; Tailscale auto-connects on boot            ║
║  - SSH accepts syncoid connection                            ║
║  - ZFS receives incremental dataset stream                   ║
║  - Optional: backupd agent on port 9999                      ║
║  - Shuts down after backup via SSH or backupd /shutdown      ║
╚══════════════════════════════════════════════════════════════╝
               │
               │  SATA / USB3
               ▼
╔══════════════════════════════════════════════════════════════╗
║                OFF-SITE BACKUP DISKS                         ║
║  - Internal 3.5" HDD or USB3 UASP drive                      ║
║  - ZFS pool (mirror or single disk)                          ║
║  - Spins down when idle                                      ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Component Map

### Orchestrator (`src/hozo/`)

| Module | Responsibility |
|--------|---------------|
| `core/wol.py` | Send WOL magic packet via UDP broadcast |
| `core/ssh.py` | TCP port polling; paramiko-based command execution |
| `core/backup.py` | syncoid subprocess wrapper with retry + dry-run |
| `core/job.py` | Full job lifecycle: WOL → SSH wait → syncoid → verify → shutdown |
| `config/loader.py` | YAML load, schema validation, `BackupJob` construction |
| `scheduler/runner.py` | APScheduler-based cron scheduler; schedule string parser |
| `notifications/notify.py` | ntfy.sh / Pushover / SMTP dispatchers |
| `api/routes.py` | FastAPI app: HTML dashboard + JSON API |
| `api/models.py` | Pydantic request/response models |
| `api/templates/` | Jinja2 + Tailwind CSS + HTMX templates |
| `cli.py` | Click CLI: jobs list/run, status, wake, shutdown, serve |

### Remote Agent (`src/backupd/`)

| Module | Responsibility |
|--------|---------------|
| `zfs.py` | `zpool status` parsing, pool export, disk spin-state (hdparm) |
| `system.py` | Uptime query; safe shutdown (export pools → `shutdown -h now`) |
| `server.py` | FastAPI micro-server: `/ping`, `/status`, `/shutdown`, `/disk/{dev}` |

---

## Backup Job Workflow

```
1. Scheduler triggers at configured time (or CLI: hozo jobs run <name>)
   │
2. WOL: send UDP magic packet to MAC address
   │  (broadcast to 255.255.255.255 or Tailscale peer IP)
   │
3. SSH wait: poll TCP :22 every 5s until available or timeout
   │  (timeout configurable per job, default 120s)
   │
4. Syncoid: run `syncoid [--recursive] source user@host:target`
   │  Handles incremental ZFS send/receive
   │  Retries up to N times with configurable delay
   │
5. Verify: list remote snapshots via SSH `zfs list -t snapshot`
   │
6. Notify: send result to configured channels (ntfy / Pushover / email)
   │
7. Shutdown: SSH `shutdown -h now` if shutdown_after=true
```

---

## Network Requirements

### Option A: Tailscale (Recommended)

- Install Tailscale on both machines
- WOL via Tailscale MagicDNS or subnet router
- SSH over Tailscale IP (100.x.x.x)
- Zero NAT/firewall configuration needed

### Option B: WireGuard

- Manual WireGuard setup between main server and remote box
- WOL via WireGuard-routed subnet if remote is on different LAN
- SSH over WireGuard interface IP

### Option C: Direct LAN

- Both machines on same network
- WOL via LAN broadcast
- No VPN needed

---

## Hardware Recommendations

| Use Case | Recommendation | Notes |
|----------|---------------|-------|
| Best overall | Dell OptiPlex Micro / HP EliteDesk Mini | WOL support, SATA bay, ZFS-capable |
| Cheapest | Raspberry Pi 4/5 + USB3 UASP HDD | Low power, ZFS runs fine |
| Thin client | HP t730, Dell Wyse 5070 Extended | Takes 3.5" HDD, low noise |

**ZFS minimum:** 1 GB RAM per 1 TB of storage (for ARC), 4 GB+ recommended.

---

## Security Model

- All transfers over SSH (encrypted)
- No passwords: SSH key authentication only (store key at `~/.ssh/`)
- Container runs with `NET_ADMIN` cap only (for WOL UDP broadcast)
- No inbound ports opened on the remote box (SSH only)
- `backupd` agent is optional and runs on a non-standard port (9999)
- Tailscale ACLs can restrict access to the backup box to only the orchestrator IP
