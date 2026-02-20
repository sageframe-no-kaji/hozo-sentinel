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


def write_config(path: Path, config: dict[str, Any]) -> None:
    """
    Atomically write a config dict to a YAML file.

    Uses a temp-file + os.replace so a crash mid-write never leaves a
    half-written file.

    Args:
        path: Destination config.yaml path.
        config: Full config dict (settings + auth + jobs).
    """
    tmp = path.with_suffix(".yaml.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp, path)
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
