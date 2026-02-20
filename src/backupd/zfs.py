"""ZFS pool inspection and management for the remote backup agent."""

import logging
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r"^\s*state:\s+(\S+)", re.MULTILINE)


def get_pool_status(pool: Optional[str] = None) -> dict:
    """
    Return health information for one or all ZFS pools.

    Args:
        pool: Specific pool name, or None for all pools

    Returns:
        Dict mapping pool name → state string ("ONLINE", "DEGRADED", "FAULTED", etc.)
    """
    cmd = ["zpool", "status"]
    if pool:
        cmd.append(pool)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return _parse_pool_status(result.stdout)
    except FileNotFoundError:
        logger.error("zpool not found — is ZFS installed?")
        return {}
    except subprocess.TimeoutExpired:
        logger.error("zpool status timed out")
        return {}


def _parse_pool_status(output: str) -> dict:
    """Parse `zpool status` output into {pool_name: state} dict."""
    pools: dict[str, str] = {}
    current_pool: Optional[str] = None
    for line in output.splitlines():
        pool_match = re.match(r"^\s*pool:\s+(\S+)", line)
        if pool_match:
            current_pool = pool_match.group(1)
        state_match = re.match(r"^\s*state:\s+(\S+)", line)
        if state_match and current_pool:
            pools[current_pool] = state_match.group(1)
    return pools


def list_pools() -> list[str]:
    """Return a list of imported ZFS pool names."""
    try:
        result = subprocess.run(
            ["zpool", "list", "-H", "-o", "name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    except Exception as exc:
        logger.error("Failed to list pools: %s", exc)
        return []


def export_pool(name: str) -> bool:
    """
    Safely export a ZFS pool (flushes all pending writes first).

    Args:
        name: Pool name

    Returns:
        True if export succeeded
    """
    logger.info("Exporting ZFS pool: %s", name)
    try:
        result = subprocess.run(
            ["zpool", "export", name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error("zpool export %s failed: %s", name, result.stderr.strip())
            return False
        logger.info("Pool %s exported successfully", name)
        return True
    except Exception as exc:
        logger.error("Exception exporting pool %s: %s", name, exc)
        return False


def disk_spin_state(device: str) -> str:
    """
    Query hard disk spin state using hdparm -C.

    Args:
        device: Block device path (e.g., "/dev/sda")

    Returns:
        One of: "active/idle", "standby", "sleeping", "unknown"
    """
    try:
        result = subprocess.run(
            ["hdparm", "-C", device],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if "drive state is:" in line.lower():
                return line.split(":")[-1].strip()
        return "unknown"
    except FileNotFoundError:
        return "hdparm not available"
    except Exception as exc:
        logger.debug("disk_spin_state: %s", exc)
        return "unknown"
