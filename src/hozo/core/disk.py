"""
Remote drive spin-up utilities for the Hōzō orchestrator.

The orchestrator runs on one machine and coordinates backups to a separate
mini-PC (NUC / USFF) with an external USB/SATA drive.  Before invoking
syncoid the orchestrator needs to ensure that drive is spun up on the
remote box, otherwise ZFS pool import and syncoid will time out.

This module SSHes into the remote backup machine and calls the same
``hdparm``/``dd``/sysfs logic that ``backupd.disk`` uses locally.
"""

import logging
import time
from typing import Optional

from hozo.core.ssh import run_command

logger = logging.getLogger(__name__)


# ── Remote state queries ───────────────────────────────────────────────────────


def remote_drive_state(
    host: str,
    device: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_key: Optional[str] = None,
) -> str:
    """
    Query the spin state of a drive on the remote backup machine via SSH.

    Runs ``hdparm -C <device>`` remotely and parses the output.
    Falls back to ``"unknown"`` if hdparm is unavailable or the host is
    unreachable.

    Args:
        host: Backup machine hostname or IP
        device: Block device path on remote, e.g. ``/dev/sda``
        ssh_user: SSH user (default ``"root"``)
        ssh_port: SSH port (default 22)
        ssh_key: Path to private key file, or None

    Returns:
        One of ``"active/idle"``, ``"standby"``, ``"sleeping"``, ``"unknown"``
    """
    cmd = f"hdparm -C {device} 2>/dev/null || echo 'hdparm_unavailable'"
    try:
        rc, stdout, _ = run_command(
            host,
            cmd,
            user=ssh_user,
            port=ssh_port,
            key_path=ssh_key,
        )
        for line in stdout.splitlines():
            if "drive state is:" in line.lower():
                return line.split(":", 1)[-1].strip().lower()
        if "hdparm_unavailable" in stdout:
            return "hdparm_unavailable"
        return "unknown"
    except Exception as exc:
        logger.debug("remote_drive_state failed for %s:%s — %s", host, device, exc)
        return "unknown"


def is_remote_drive_active(
    host: str,
    device: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_key: Optional[str] = None,
) -> bool:
    """
    Return True if the remote drive is spinning and ready for I/O.

    Args:
        host: Backup machine hostname or IP
        device: Block device path on remote, e.g. ``/dev/sda``
        ssh_user: SSH user
        ssh_port: SSH port
        ssh_key: Path to private key file, or None
    """
    state = remote_drive_state(host, device, ssh_user, ssh_port, ssh_key)
    return state in ("active/idle", "active", "idle", "unknown", "hdparm_unavailable")


# ── Remote spin-up ─────────────────────────────────────────────────────────────


def remote_spin_up_drive(
    host: str,
    device: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_key: Optional[str] = None,
) -> bool:
    """
    SSH to the remote machine and perform a small harmless sector read to spin
    up a standby/sleeping external drive.

    Uses ``dd if=<device> bs=4096 count=1 of=/dev/null`` which is safe
    (read-only) and works without hdparm.

    Args:
        host: Backup machine hostname or IP
        device: Block device path on remote, e.g. ``/dev/sda``
        ssh_user: SSH user
        ssh_port: SSH port
        ssh_key: Path to private key file, or None

    Returns:
        True if the remote dd command succeeded
    """
    cmd = f"dd if={device} bs=4096 count=1 of=/dev/null 2>/dev/null"
    logger.info("Spinning up remote drive %s on %s…", device, host)
    try:
        rc, _out, err = run_command(
            host,
            cmd,
            user=ssh_user,
            port=ssh_port,
            key_path=ssh_key,
        )
        if rc == 0:
            logger.info("Remote drive %s on %s responded to spin-up read", device, host)
            return True
        logger.warning("Remote spin-up dd failed (rc=%d): %s", rc, err.strip())
        return False
    except Exception as exc:
        logger.error("remote_spin_up_drive error: %s", exc)
        return False


# ── Wait for remote drive to become active ─────────────────────────────────────


def wait_for_remote_drive_active(
    host: str,
    device: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_key: Optional[str] = None,
    timeout: int = 60,
    poll_interval: float = 5.0,
    spin_up_on_standby: bool = True,
) -> bool:
    """
    Poll the remote drive until it reports an active/idle state.

    On the first poll, if the drive is in standby and ``spin_up_on_standby`` is
    True, a remote sector-read is issued immediately to kick the motor.

    This should be called *after* SSH is confirmed up (i.e. after
    ``wait_for_ssh`` returns True) but *before* syncoid is invoked.  On a
    typical 2.5″ USB drive, spin-up from cold takes 5–15 seconds.

    Args:
        host: Backup machine hostname or IP
        device: Block device path on remote, e.g. ``/dev/sda``
        ssh_user: SSH login user
        ssh_port: SSH port
        ssh_key: Path to private key file, or None
        timeout: Maximum seconds to wait for drive to become active
        poll_interval: Seconds between state polls
        spin_up_on_standby: Issue a spin-up read immediately when standby detected

    Returns:
        True if drive became active before ``timeout`` elapsed
    """
    logger.info("Waiting for remote drive %s on %s (timeout: %ds)…", device, host, timeout)
    deadline = time.monotonic() + timeout
    first_poll = True

    while time.monotonic() < deadline:
        if is_remote_drive_active(host, device, ssh_user, ssh_port, ssh_key):
            logger.info("Remote drive %s on %s is active", device, host)
            return True

        if first_poll and spin_up_on_standby:
            logger.info("Drive %s on %s is standby — issuing remote spin-up read", device, host)
            remote_spin_up_drive(host, device, ssh_user, ssh_port, ssh_key)
            first_poll = False

        remaining = deadline - time.monotonic()
        logger.debug("Remote drive not yet active, %.0fs remaining…", remaining)
        time.sleep(min(poll_interval, max(0, remaining)))

    logger.error("Remote drive %s on %s did not become active within %ds", device, host, timeout)
    return False
