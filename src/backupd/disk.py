"""
External hard drive spin-up detection and management for the backup agent.

This module runs on the backup mini-PC (NUC / USFF box) and is responsible for
detecting whether an external USB/SATA drive is spun up and ready for I/O
before a ZFS sync begins.  Spinning up a cold drive before syncoid fires
prevents timeout errors and pool import failures.

Typical call sequence (from backupd /status endpoint or via SSH):
    state  = get_drive_state("/dev/sda")   # "standby" | "active/idle" | …
    if not is_drive_active("/dev/sda"):
        spin_up_drive("/dev/sda")
    ready  = wait_for_drive_active("/dev/sda", timeout=60)
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── State helpers ──────────────────────────────────────────────────────────────


def get_drive_state(device: str) -> str:
    """
    Return the power/spin state of a hard drive.

    Tries ``hdparm -C`` first; falls back to reading
    ``/sys/block/<name>/queue/rotational`` + stat if hdparm is absent.

    Args:
        device: Block device path, e.g. ``/dev/sda``

    Returns:
        One of ``"active/idle"``, ``"standby"``, ``"sleeping"``,
        ``"unknown"``, or ``"hdparm_unavailable"``
    """
    # ── Method 1: hdparm -C ───────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["hdparm", "-C", device],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if "drive state is:" in line.lower():
                return line.split(":", 1)[-1].strip().lower()
        # hdparm ran but gave no state line — device may be NVMe or non-rotational
        return "unknown"
    except FileNotFoundError:
        logger.debug("hdparm not found, falling back to /sys")

    # ── Method 2: /sys/block/<dev>/stat IO counter snapshot ──────────────────
    # If the disk has non-zero read I/O completions recently it is likely active.
    # We can't reliably detect *standby* this way, so we just return "unknown".
    dev_name = Path(device).name  # e.g. "sda"
    stat_path = Path(f"/sys/block/{dev_name}/stat")
    if stat_path.exists():
        try:
            stat_path.read_bytes()  # this read itself wakes the drive if it can
            return "unknown"  # we triggered a read but can't determine prior state
        except Exception as exc:
            logger.debug("stat read failed: %s", exc)

    return "hdparm_unavailable"


def is_drive_active(device: str) -> bool:
    """
    Return True if the drive is spinning and ready for I/O.

    A drive in ``"active/idle"`` state is ready.  ``"standby"`` and
    ``"sleeping"`` mean the platters have stopped; any I/O will take 5–15 s
    for the motor to spin back up.

    Args:
        device: Block device path, e.g. ``/dev/sda``
    """
    state = get_drive_state(device)
    # "active/idle" is the normal running state
    # If hdparm is unavailable we optimistically assume the drive is ready
    return state in ("active/idle", "active", "idle", "unknown", "hdparm_unavailable")


def spin_up_drive(device: str) -> bool:
    """
    Kick a standby/sleeping drive awake by performing a harmless small read.

    Reads the first 4 KiB sector (discards data) to force the drive motor to
    spin up.  This is a no-op if the drive is already active.

    Args:
        device: Block device path, e.g. ``/dev/sda``

    Returns:
        True if the read command succeeded (drive responded), False otherwise
    """
    logger.info("Spinning up drive %s via sector read…", device)
    try:
        result = subprocess.run(
            ["dd", f"if={device}", "bs=4096", "count=1", "of=/dev/null"],
            capture_output=True,
            text=True,
            timeout=60,  # spinning from cold can take up to 20-30 s on some drives
        )
        if result.returncode == 0:
            logger.info("Drive %s responded to spin-up read", device)
            return True
        logger.warning("spin_up_drive dd returned %d: %s", result.returncode, result.stderr.strip())
        return False
    except subprocess.TimeoutExpired:
        logger.error("Drive %s did not respond within spin-up timeout", device)
        return False
    except Exception as exc:
        logger.error("spin_up_drive error: %s", exc)
        return False


def wait_for_drive_active(
    device: str,
    timeout: int = 60,
    poll_interval: float = 3.0,
    spin_up_on_standby: bool = True,
) -> bool:
    """
    Poll until the drive reports an active/idle state or ``timeout`` seconds
    have elapsed.

    On the first call, if the drive is standby and ``spin_up_on_standby`` is
    True, a sector-read is issued immediately to kick the motor.

    Args:
        device: Block device path, e.g. ``/dev/sda``
        timeout: Maximum seconds to wait
        poll_interval: Seconds between state polls
        spin_up_on_standby: If True, issue a ``spin_up_drive`` call when
            standby/sleeping is detected on the first poll

    Returns:
        True if the drive became active before ``timeout`` expired
    """
    logger.info("Waiting for drive %s to become active (timeout: %ds)…", device, timeout)
    deadline = time.monotonic() + timeout
    first_poll = True

    while time.monotonic() < deadline:
        if is_drive_active(device):
            logger.info("Drive %s is active and ready", device)
            return True

        if first_poll and spin_up_on_standby:
            logger.info("Drive %s is in standby — issuing spin-up read", device)
            spin_up_drive(device)
            first_poll = False

        remaining = deadline - time.monotonic()
        logger.debug("Drive %s not yet active, %.0fs remaining…", device, remaining)
        time.sleep(min(poll_interval, max(0, remaining)))

    logger.error("Drive %s did not become active within %ds", device, timeout)
    return False


# ── /sys/block stat-based activity probe (alternative, no hdparm) ─────────────


def _read_io_completions(device: str) -> Optional[int]:
    """
    Read the cumulative read-I/O-completion counter from sysfs.

    Returns None if the stat file is not available.
    """
    dev_name = Path(device).name
    stat_path = Path(f"/sys/block/{dev_name}/stat")
    if not stat_path.exists():
        return None
    try:
        fields = stat_path.read_text().split()
        # /sys/block/sda/stat field 0 = reads completed successfully
        return int(fields[0])
    except Exception:
        return None


def has_recent_io_activity(device: str, probe_interval: float = 1.0) -> Optional[bool]:
    """
    Detect I/O activity via back-to-back sysfs stat counter reads.

    Takes two snapshots separated by ``probe_interval`` seconds and returns
    True if the read-completion counter changed (meaning the disk serviced at
    least one I/O during that window).

    Returns None if sysfs stat is not available (e.g. on macOS or virtual disks).

    This is a lightweight alternative to hdparm that works without root,
    but it only detects *activity*, not spin state.

    Args:
        device: Block device path, e.g. ``/dev/sda``
        probe_interval: Seconds between the two stat snapshots
    """
    before = _read_io_completions(device)
    if before is None:
        return None
    time.sleep(probe_interval)
    after = _read_io_completions(device)
    if after is None:
        return None
    return after != before


# ── Convenience: get a full drive summary dict (used by backupd /status) ──────


def drive_summary(device: str) -> dict:
    """
    Return a dict with all available drive metrics for the given device.

    Keys: ``device``, ``state``, ``active``, ``io_counter``

    Args:
        device: Block device path, e.g. ``/dev/sda``
    """
    state = get_drive_state(device)
    io = _read_io_completions(device)
    return {
        "device": device,
        "state": state,
        "active": is_drive_active(device),
        "io_completions": io,
    }
