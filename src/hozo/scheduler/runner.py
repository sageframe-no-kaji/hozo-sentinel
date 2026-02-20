"""APScheduler-based job scheduler for Hōzō."""

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from hozo.config.loader import jobs_from_config, load_config
from hozo.core.job import BackupJob, JobResult, run_job

logger = logging.getLogger(__name__)

_WEEKLY_RE = re.compile(
    r"^weekly\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(\d{2}):(\d{2})$",
    re.IGNORECASE,
)
_DAILY_RE = re.compile(r"^daily\s+(\d{2}):(\d{2})$", re.IGNORECASE)

_DOW_MAP = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


def parse_schedule(schedule_str: str) -> CronTrigger:
    """
    Parse a human-readable schedule string into an APScheduler CronTrigger.

    Supported formats:
        "daily HH:MM"          — runs every day at HH:MM
        "weekly <Day> HH:MM"   — runs once a week on <Day> at HH:MM

    Args:
        schedule_str: Schedule string (e.g., "weekly Sunday 03:00")

    Returns:
        CronTrigger instance

    Raises:
        ValueError: If the schedule string is not recognized
    """
    weekly = _WEEKLY_RE.match(schedule_str.strip())
    if weekly:
        day_name, hh, mm = weekly.group(1), weekly.group(2), weekly.group(3)
        dow = _DOW_MAP[day_name.lower()]
        return CronTrigger(day_of_week=dow, hour=int(hh), minute=int(mm))

    daily = _DAILY_RE.match(schedule_str.strip())
    if daily:
        hh, mm = daily.group(1), daily.group(2)
        return CronTrigger(hour=int(hh), minute=int(mm))

    raise ValueError(
        f"Unrecognized schedule format: '{schedule_str}'. "
        "Expected 'daily HH:MM' or 'weekly <Day> HH:MM'."
    )


class HozoScheduler:
    """
    Manages scheduled backup jobs using APScheduler.

    Usage::

        scheduler = HozoScheduler()
        scheduler.load_jobs_from_config(Path("config.yaml"))
        scheduler.start()
        # ... runs in background ...
        scheduler.stop()
    """

    def __init__(self, on_result: Optional[Callable[[JobResult], None]] = None) -> None:
        """
        Args:
            on_result: Optional callback invoked after each job completes with its JobResult.
        """
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._jobs: list[BackupJob] = []
        self._on_result = on_result

    def load_jobs_from_config(self, config_path: Path) -> int:
        """
        Load jobs from a YAML config file and register them with the scheduler.

        Args:
            config_path: Path to hozo config.yaml

        Returns:
            Number of jobs registered
        """
        config = load_config(config_path)
        if not config:
            logger.warning("Config at %s is empty, no jobs loaded", config_path)
            return 0

        raw_jobs = config.get("jobs", [])
        self._jobs = jobs_from_config(config)

        registered = 0
        for job, raw in zip(self._jobs, raw_jobs):
            schedule_str = raw.get("schedule", "")
            if not schedule_str:
                logger.info("Job '%s' has no schedule — skipping auto-scheduling", job.name)
                continue
            try:
                trigger = parse_schedule(schedule_str)
            except ValueError as exc:
                logger.error("Could not parse schedule for job '%s': %s", job.name, exc)
                continue

            self._scheduler.add_job(
                func=self._run_job_wrapper,
                trigger=trigger,
                args=[job],
                id=job.name,
                name=job.name,
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info("Scheduled job '%s' with trigger: %s", job.name, schedule_str)
            registered += 1

        return registered

    def schedule_job(self, job: BackupJob, trigger: Any, job_id: Optional[str] = None) -> str:
        """
        Register a single BackupJob with an arbitrary APScheduler trigger.

        Useful for:
        - Integration tests (``DateTrigger`` set 1 s in the future)
        - One-off "run at this exact datetime" scheduling from the CLI

        Args:
            job: BackupJob configuration
            trigger: Any APScheduler trigger instance
                (CronTrigger, DateTrigger, IntervalTrigger, …)
            job_id: Explicit job id; defaults to ``job.name``

        Returns:
            The APScheduler job id used
        """
        jid = job_id or job.name
        if job not in self._jobs:
            self._jobs.append(job)
        self._scheduler.add_job(
            func=self._run_job_wrapper,
            trigger=trigger,
            args=[job],
            id=jid,
            name=job.name,
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Scheduled job '%s' with trigger %s", job.name, trigger)
        return jid

    def _run_job_wrapper(self, job: BackupJob) -> None:
        """Execute a job and invoke the result callback."""
        result = run_job(job)
        if self._on_result:
            try:
                self._on_result(result)
            except Exception as exc:
                logger.error("on_result callback raised: %s", exc)

    def run_job_now(self, job_name: str) -> Optional[JobResult]:
        """
        Immediately execute a job by name (blocking).

        Args:
            job_name: Name of the job as defined in config

        Returns:
            JobResult, or None if job_name not found
        """
        for job in self._jobs:
            if job.name == job_name:
                return run_job(job)
        logger.error("Job '%s' not found", job_name)
        return None

    @property
    def jobs(self) -> list[BackupJob]:
        return list(self._jobs)

    def start(self) -> None:
        """Start the background scheduler."""
        self._scheduler.start()
        logger.info("Scheduler started with %d job(s)", len(self._scheduler.get_jobs()))

    def stop(self, wait: bool = True) -> None:
        """Stop the background scheduler."""
        self._scheduler.shutdown(wait=wait)
        logger.info("Scheduler stopped")
