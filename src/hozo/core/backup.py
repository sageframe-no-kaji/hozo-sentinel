"""Syncoid ZFS replication wrapper."""

import logging
import shlex
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class SyncoidError(Exception):
    """Raised when syncoid exits with a non-zero status."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"syncoid exited {returncode}: {stderr.strip()}")


def run_syncoid(
    source_dataset: str,
    target_host: str,
    target_dataset: str,
    recursive: bool = True,
    ssh_user: str = "root",
    ssh_key: Optional[str] = None,
    no_privilege_elevation: bool = False,
    dry_run: bool = False,
    syncoid_bin: str = "syncoid",
) -> bool:
    """
    Run syncoid to replicate a ZFS dataset to a remote host.

    Args:
        source_dataset: Local ZFS dataset (e.g., "rpool/data")
        target_host: Remote hostname or Tailscale address
        target_dataset: Remote ZFS dataset (e.g., "backup/rpool-data")
        recursive: Whether to replicate child datasets (--recursive)
        ssh_user: SSH user on the remote host (default: root)
        ssh_key: Path to SSH private key file (optional)
        no_privilege_elevation: Pass --no-privilege-elevation to syncoid
        dry_run: If True, print the command without executing it
        syncoid_bin: Path or name of the syncoid binary

    Returns:
        True if replication succeeded

    Raises:
        SyncoidError: If syncoid exits with a non-zero status
        FileNotFoundError: If syncoid is not found in PATH
    """
    cmd = [syncoid_bin]

    if recursive:
        cmd.append("--recursive")
    if no_privilege_elevation:
        cmd.append("--no-privilege-elevation")

    # Build SSH options
    ssh_opts_parts: list[str] = ["-o StrictHostKeyChecking=no"]
    if ssh_key:
        ssh_opts_parts.append(f"-i {ssh_key}")
    cmd.extend(["--sshcipher", "aes128-ctr"])
    cmd.extend(["--sshoption", " ".join(ssh_opts_parts)])

    source = source_dataset
    target = f"{ssh_user}@{target_host}:{target_dataset}"

    cmd.extend([source, target])

    logger.info("Running syncoid: %s", shlex.join(cmd))

    if dry_run:
        logger.info("[DRY RUN] Would run: %s", shlex.join(cmd))
        return True

    result = subprocess.run(
        cmd,
        capture_output=False,
        text=True,
        timeout=3600,  # 1-hour hard timeout per job
    )

    if result.returncode != 0:
        raise SyncoidError(result.returncode, result.stderr or "")

    logger.info(
        "Syncoid completed successfully for %s → %s:%s", source, target_host, target_dataset
    )
    return True


def list_remote_snapshots(
    host: str,
    dataset: str,
    ssh_user: str = "root",
    ssh_key: Optional[str] = None,
) -> list[str]:
    """
    List ZFS snapshot names on the remote host for the given dataset.

    Args:
        host: Remote hostname
        dataset: ZFS dataset path
        ssh_user: SSH user
        ssh_key: Path to SSH private key

    Returns:
        List of snapshot names (e.g., ["rpool/data@2024-01-01", ...])
    """
    from hozo.core.ssh import run_command

    cmd = f"zfs list -H -o name -t snapshot -r {shlex.quote(dataset)}"
    exit_code, stdout, stderr = run_command(host, cmd, user=ssh_user, key_path=ssh_key)
    if exit_code != 0:
        logger.warning("Failed to list snapshots on %s:%s — %s", host, dataset, stderr.strip())
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]
