"""Atomic YAML config write-back for Hōzō."""

import os
from pathlib import Path
from typing import Any, Optional

import yaml

from hozo.core.job import BackupJob


def job_to_raw(job: BackupJob) -> dict[str, Any]:
    """Serialize a BackupJob back to the raw YAML dict format the loader expects."""
    d: dict[str, Any] = {
        "name": job.name,
        "source": job.source_dataset,
        "target_host": job.target_host,
        "target_dataset": job.target_dataset,
        "mac_address": job.mac_address,
        "ssh_user": job.ssh_user,
        "ssh_port": job.ssh_port,
        "recursive": job.recursive,
        "shutdown_after": job.shutdown_after,
        "ssh_timeout": job.timeout,
        "retries": job.retries,
        "retry_delay": job.retry_delay,
        "broadcast_ip": job.wol_broadcast,
        "no_privilege_elevation": job.no_privilege_elevation,
        "description": job.description,
    }
    if job.ssh_key:
        d["ssh_key"] = job.ssh_key
    if job.schedule:
        d["schedule"] = job.schedule
    if job.backup_device:
        d["backup_device"] = job.backup_device
        d["disk_spinup_timeout"] = job.disk_spinup_timeout
    return d


def _dump_yaml(f: Any, config: dict[str, Any]) -> None:
    yaml.dump(
        config,
        f,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def write_config(path: Path, config: dict[str, Any]) -> None:
    """
    Write a config dict to a YAML file, atomically when possible.

    Default path: temp-file + os.replace so a crash mid-write never leaves a
    half-written file.

    Fallback for single-file bind mounts (Docker mounts config.yaml directly,
    not its parent directory): os.replace from a sibling temp file fails
    because the target is the mount point itself.  In that case fall back to
    an in-place truncate-and-write — non-atomic, but the only option when the
    parent directory inside the container isn't writable as a whole.

    Args:
        path: Destination config.yaml path.
        config: Full config dict (settings + auth + jobs).
    """
    tmp = path.with_suffix(".yaml.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the new contents in tmp first. A failure here (disk full,
        # yaml encoding error) must not touch the live file.
        with open(tmp, "w", encoding="utf-8") as f:
            _dump_yaml(f, config)
        # The config holds the session secret and notification credentials —
        # restrict to owner-only before it becomes the live file.
        os.chmod(tmp, 0o600)
        try:
            os.replace(tmp, path)
        except OSError:
            # Single-file bind mount or cross-device rename — atomic replace
            # is not available. Fall back to copying tmp's contents over the
            # existing inode in place. Less crash-safe than atomic replace,
            # but tmp is fully written and validated by this point, so the
            # window where path is partially written is just the copy itself.
            with open(tmp, "rb") as src, open(path, "wb") as dst:
                dst.write(src.read())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass  # chmod may not be supported on the mounted FS
            tmp.unlink(missing_ok=True)
    except Exception:
        # Clean up temp file on failure
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def build_config_dict(
    jobs: list[BackupJob],
    settings: Optional[dict[str, Any]] = None,
    auth: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Build a full config dict from jobs + settings + auth sections.

    Args:
        jobs: Current BackupJob list.
        settings: Raw settings dict (ssh_timeout, ssh_user, notifications …).
        auth: Raw auth dict (rp_id, session_secret, credentials …).

    Returns:
        Config dict ready for write_config().
    """
    result: dict[str, Any] = {}
    if settings:
        result["settings"] = settings
    if auth:
        result["auth"] = auth
    result["jobs"] = [job_to_raw(j) for j in jobs]
    return result
