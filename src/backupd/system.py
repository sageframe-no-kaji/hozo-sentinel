"""System-level operations for the remote backup agent."""

import logging
import subprocess
import time
from pathlib import Path

from backupd.zfs import export_pool, list_pools

logger = logging.getLogger(__name__)


def get_uptime() -> float:
    """
    Return system uptime in seconds.

    Reads /proc/uptime on Linux; falls back to 0.0 on other platforms.
    """
    uptime_path = Path("/proc/uptime")
    if uptime_path.exists():
        try:
            return float(uptime_path.read_text().split()[0])
        except Exception:
            pass
    return 0.0


def safe_shutdown(export_pools: bool = True, delay_seconds: int = 2) -> bool:
    """
    Safely shut down the system.

    Steps:
        1. Export all ZFS pools (to flush writes and avoid dirty state)
        2. Wait `delay_seconds` to let the HTTP response be delivered
        3. Issue `shutdown -h now`

    Args:
        export_pools: If True, export all ZFS pools before shutdown
        delay_seconds: Grace period (seconds) before calling shutdown

    Returns:
        True if shutdown command was issued (process terminates after)
    """
    if export_pools:
        pools = list_pools()
        for pool in pools:
            logger.info("Exporting pool '%s' before shutdown…", pool)
            export_pool(pool)

    logger.info("Waiting %ds before issuing shutdown…", delay_seconds)
    time.sleep(delay_seconds)

    logger.info("Issuing 'shutdown -h now'")
    try:
        subprocess.run(
            ["shutdown", "-h", "now"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return True
    except Exception as exc:
        logger.error("Shutdown failed: %s", exc)
        return False
