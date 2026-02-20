"""YAML configuration loader and validator."""

import re
from pathlib import Path
from typing import Any, Optional

import yaml

from hozo.core.job import BackupJob

# Accepted schedule formats:
#   "weekly <Weekday> HH:MM"   e.g. "weekly Sunday 03:00"
#   "daily HH:MM"              e.g. "daily 02:30"
_WEEKLY_RE = re.compile(
    r"^weekly\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{2}:\d{2})$",
    re.IGNORECASE,
)
_DAILY_RE = re.compile(r"^daily\s+(\d{2}:\d{2})$", re.IGNORECASE)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")


class ConfigError(Exception):
    """Raised for invalid or missing configuration."""


def load_config(path: Path) -> Optional[dict[str, Any]]:
    """
    Load configuration from a YAML file.

    Args:
        path: Path to the YAML config file

    Returns:
        Parsed configuration dictionary, or None if file is empty

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML is invalid
    """
    with open(path) as f:
        result: Optional[dict[str, Any]] = yaml.safe_load(f)
        return result


def validate_config(config: dict[str, Any]) -> list[str]:
    """
    Validate a loaded configuration dictionary.

    Returns:
        List of validation error messages (empty list = valid)
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config root must be a YAML mapping"]

    jobs = config.get("jobs")
    if not jobs:
        errors.append("'jobs' key is required and must be a non-empty list")
        return errors

    if not isinstance(jobs, list):
        errors.append("'jobs' must be a list")
        return errors

    required_fields = ["name", "source", "target_host", "target_dataset", "mac_address"]
    for i, job in enumerate(jobs):
        prefix = f"jobs[{i}]"
        if not isinstance(job, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        for field in required_fields:
            if not job.get(field):
                errors.append(f"{prefix}: missing required field '{field}'")
        mac = job.get("mac_address", "")
        if mac and not _MAC_RE.match(mac):
            errors.append(f"{prefix}: invalid mac_address '{mac}'")
        schedule = job.get("schedule", "")
        if schedule and not (_WEEKLY_RE.match(schedule) or _DAILY_RE.match(schedule)):
            errors.append(
                f"{prefix}: unrecognized schedule '{schedule}' "
                "(expected 'weekly <Day> HH:MM' or 'daily HH:MM')"
            )

    return errors


def jobs_from_config(config: dict[str, Any]) -> list[BackupJob]:
    """
    Construct a list of BackupJob objects from a validated config dict.

    Args:
        config: Parsed and validated config dictionary

    Returns:
        List of BackupJob instances
    """
    settings = config.get("settings", {})
    default_timeout = settings.get("ssh_timeout", 120)
    default_user = settings.get("ssh_user", "root")

    jobs: list[BackupJob] = []
    for raw in config.get("jobs", []):
        jobs.append(
            BackupJob(
                name=raw["name"],
                source_dataset=raw["source"],
                target_host=raw["target_host"],
                target_dataset=raw["target_dataset"],
                mac_address=raw["mac_address"],
                ssh_user=raw.get("ssh_user", default_user),
                ssh_key=raw.get("ssh_key"),
                ssh_port=int(raw.get("ssh_port", 22)),
                recursive=bool(raw.get("recursive", True)),
                shutdown_after=bool(raw.get("shutdown_after", True)),
                timeout=int(raw.get("ssh_timeout", default_timeout)),
                retries=int(raw.get("retries", 3)),
                retry_delay=int(raw.get("retry_delay", 60)),
                wol_broadcast=raw.get("broadcast_ip", "255.255.255.255"),
                no_privilege_elevation=bool(raw.get("no_privilege_elevation", False)),
                description=raw.get("description", ""),
            )
        )
    return jobs
