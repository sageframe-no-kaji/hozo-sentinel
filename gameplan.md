### **Hōzō (宝蔵)**

**Treasure Storehouse.**
Absolutely excellent for backups.
Symbolic of hidden, precious Dharma teachings being preserved.

# Off-Site ZFS Backup Orchestrator

### _A Concept, Architecture, and Implementation Guide for a Wake-On-Demand, Syncoid-Driven Backup Mini-Server_

---

## 1. Overview

This document outlines a complete concept for a small, self-contained backup system designed for home-lab users who want **secure, automated, off-site ZFS backups** using:

- **Syncoid** (for ZFS send/receive replication)
- **Wake-on-LAN (WOL)** (to wake the remote backup host only when needed)
- **A small mini-PC / micro-PC / or Pi** (running ZFS + SSH + a lightweight agent)
- **A controller container** running on the main server
- **A VPN overlay (Tailscale / WireGuard)** for secure communication

The idea is to create a **simple, robust orchestrator** that wakes a remote machine, waits for it to come online, performs incremental ZFS replication, verifies integrity, and powers the remote system back down.

The orchestrator could be built as a **Dockerized application** with:

- A simple **CLI interface**
- Optional **minimal web UI**
- A YAML-based job definition format
- A small, lightweight “agent” on the remote node

This would solve one of the biggest pain points in home/self-hosted ZFS setups:
**fully automated, intelligent, off-site backup workflows that don’t require a full always-on remote NAS or server.**

---

## 2. Motivating Principles

1. **Most people do NOT want a second always-on NAS.**
   A small sleeper box (Pi, thin client, or micro-PC) is cheaper + quieter + easier.

2. **Remote locations often have slow or unreliable power and networking.**
   The box must be able to sleep, wake, update, and shut down safely.

3. **Incremental ZFS replication is extremely efficient**, but only if:

   - The system is powered when needed
   - The network path is secure
   - Snapshots run reliably
   - Conflicts and errors are handled gracefully

4. **Backup orchestration is currently too manual**:

   - Wake host
   - SSH test
   - Mount drive
   - Run Syncoid
   - Verify
   - Send health status
   - Sleep or power down

   A smart controller can handle all of this.

---

## 3. System Architecture

```
╔══════════════════════════════════════════════════════╗
║                MAIN SERVER (Home / Office)           ║
║  - Proxmox node or Linux server                      ║
║  - Docker container: “Backup Orchestrator”           ║
║  - Holds job definitions, keys, schedules            ║
║  - Sends Wake-on-LAN magic packets                   ║
║  - Runs Syncoid over VPN                             ║
║  - Verifies replication & reports status             ║
╚══════════════════════════════════════════════════════╝
```

                ⇣ WOL (LAN or Tailscale MagicDNS)

```
╔══════════════════════════════════════════════════════╗
║                REMOTE BACKUP MINI-SERVER             ║
║  - Micro PC / Mini PC / RasPi with ZFS               ║
║  - Lightweight agent (“backupd”) listening on TCP    ║
║  - Exposes simple RPC: wake ack, status, shutdown    ║
║  - Runs ZFS, SSH, Syncoid receiver                   ║
║  - Spins down disks when idle                        ║
╚══════════════════════════════════════════════════════╝
```

                 ⇣ Secure Channel (Tailscale / WireGuard)

```
╔════════════════════════════════════════════════════════╗
║                 OFF-SITE BACKUP DISKS                  ║
║  - Internal 3.5” HDD or USB3 UASP drive                ║
║  - Can be scheduled to spin down                       ║
║  - Only powered during backup windows                  ║
╚════════════════════════════════════════════════════════╝
```

---

## 4. Backup Workflow (Ideal Behavior)

### **1. Orchestrator checks schedule**

- Reads YAML job (e.g., weekly, nightly, custom)
- Gathers source dataset list
- Checks last replication state

### **2. Orchestrator wakes remote node**

- Sends WOL magic packet
- Pings via VPN
- Performs SSH readiness tests

### **3. Ensures remote disks are up**

- Polls for ZFS pool import
- If external USB: waits for driver + mount
- Local agent returns “ready”

### **4. Runs Syncoid**

- `syncoid sourcePool/dataset remoteHost:backupPool/dataset`
- Handles incremental ZFS send/recv
- Resumes incomplete jobs if supported

### **5. Verifies**

- Compares snapshot lists
- Checks replication integrity
- Ensures no pending errors in zpool scrub status

### **6. Sends health status**

- Console logs
- Web UI status badge
- Optional email / Pushover / ntfy.sh

### **7. Powers down remote node**

- Sends shutdown RPC to agent
- Remote ZFS pool is safely exported
- Remote machine sleeps or fully powers off

---

## 5. Proposed Components & Tools

### **A. Controller (Docker Container)**

Runs on your main machine.

**Languages**

- Python 3.12 (perfect for CLI + async networking)
- Rust optional for high performance

**Frameworks**

- Click (CLI)
- FastAPI or Flask (optional web UI)
- schedule / APScheduler (cron-like tasks)

**Utilities**

- `etherwake` or Python WOL modules
- `syncoid` (must be installed in container)
- `tailscale` client baked into container
- `paramiko` for SSH automation

**Config Format (YAML)**
Example:

```yaml
jobs:
  - name: weekly_full_backup
    source: rpool/data
    target: remote:backups/rpool-data
    wake_mac: '00:11:22:33:44:55'
    wake_interface: 'eth0'
    remote_host: 'remote-box.tailnet.com'
    schedule: 'weekly Sunday 03:00'
    shutdown_after: true
    retries: 3
```

---

### **B. Remote Agent (“backupd”)**

Runs on the target machine.

**Functionality**

- Receives “ping” from controller
- Responds with status (uptime, zpool health, disk state)
- Ensures ZFS pool is imported
- Allows safe shutdown
- Optional: spin-down timers

**Language Choices**

- Python (easiest)
- Go (most reliable for services)
- Rust (overkill but sexy)

---

### **C. Network Layer**

You need:

- **Tailscale** (best)
- OR WireGuard
- OR your own VPN

Why Tailscale?

- WOL over MagicDNS
- Zero configuration networking
- NAT traversal
- Access control

---

## **6. Hardware Options for the Remote Backup Machine**

### **Option 1: Micro-PC or Tiny Desktop (Best)**

- Old Dell OptiPlex Micro
- HP EliteDesk Mini
- Lenovo Tiny
- Fit a **3.5” HDD internally**
- WOL support
- ZFS-capable
- Sleeps efficiently

### **Option 2: Raspberry Pi + USB UASP HDD**

- Cheapest, lowest power
- ZFS runs okay on Pi 4 / Pi 5
- USB drive can spin down
- Great for incremental backups only

### **Option 3: Thin Client With Internal SATA Bay**

E.g., HP t730, Dell Wyse 5070 Extended

- Quiet
- Takes 3.5” HDD
- ZFS support

---

## **7. Potential UI/CLI Features**

### **CLI Commands**

```
backupctl jobs list
backupctl jobs run weekly_full_backup
backupctl status remote
backupctl wake remote
backupctl shutdown remote
```

### **Optional Web UI**

- Minimal FastAPI + HTMX frontend
- Dashboard:
  - Next scheduled backup
  - Last snapshot replicated
  - ZPool health (local + remote)
  - Disk spin state
  - Button to manually wake & replicate

### **API**

```
POST /wake
POST /run_backup
GET /status
POST /shutdown
```

---

## **8. Why People Would Use This**

- Most home labbers want off-site backups
- Most don’t want a second full NAS
- Wake-on-demand systems save $$ and drive wear
- Existing tools require too much scripting
- Syncoid is powerful but unintuitive for beginners
- This tool solves a _real_ and _common_ pain point

This project could easily gain traction on:

- r/homelab
- r/selfhosted
- r/zfs
- Hacker News
- GitHub trending

With enough polish, it could become the de-facto way people do off-site ZFS backups for cheap.

---

## **9. Next Steps for Building It**

1. Create basic Python CLI skeleton
2. Add WOL module
3. Add SSH readiness checks
4. Wrap Syncoid calls
5. Build minimal YAML job loader
6. Create remote agent service
7. Add shutdown RPC
8. Add logging + health output
9. Package it in Docker
10. Publish on GitHub + documentation

---

## **10. Conclusion**

This project is not only viable — it fills a huge gap.

A simple, Dockerized “off-site ZFS orchestrator” with:

- Wake-on-LAN
- Syncoid automation
- Remote agent
- VPN
- Scheduled tasks
- Disk sleep support
- Web UI & CLI

…would be a **massive win** for thousands of home-lab users who want off-site ZFS backups without needing a full second server.

It’s a perfect project for you to learn:

- Docker
- Python
- APIs
- Networking
- ZFS automation
- GitHub workflows

And it’s genuinely useful.

If you want, I can now:

- Design the CLI
- Design the YAML schema
- Outline the Python package
- Build the actual Dockerfile
- Structure the remote-agent service
- Generate a full GitHub README

Just tell me what you want next.
