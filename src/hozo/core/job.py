"""Job execution orchestration."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from hozo.core.backup import SyncoidError, list_remote_snapshots, run_syncoid
from hozo.core.disk import wait_for_remote_drive_active
from hozo.core.ssh import run_command, wait_for_ssh
from hozo.core.wol import wake

logger = logging.getLogger(__name__)


@dataclass
class BackupJob:
    """Configuration for a single backup job."""

    name: str
    source_dataset: str
    target_host: str
    target_dataset: str
    mac_address: str
    ssh_user: str = "root"
    ssh_key: Optional[str] = None
    ssh_port: int = 22
    recursive: bool = True
    shutdown_after: bool = True
    timeout: int = 120
    retries: int = 3
    retry_delay: int = 60
    wol_broadcast: str = "255.255.255.255"
    no_privilege_elevation: bool = False
    description: str = ""
    # External drive device path on the backup machine (e.g. /dev/sda).
    # When set, Hōzō will wait for the drive to spin up before starting syncoid.
    backup_device: Optional[str] = None
    disk_spinup_timeout: int = 90  # seconds
    # Schedule string as stored in config ("weekly Sunday 03:00" / "daily 02:00").
    # Stored here so the UI can round-trip it without re-parsing APScheduler state.
    schedule: str = ""


@dataclass
class JobResult:
    """Result of a completed backup job execution."""

    job_name: str
    success: bool
    started_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    snapshots_after: list[str] = field(default_factory=list)
    attempts: int = 1
    log_lines: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


def run_job(job: BackupJob) -> JobResult:
    """
    Execute a complete backup job.

    Workflow:
        1. Send WOL packet
        2. Wait for SSH to become available
        2.5 Wait for the external drive to spin up (if backup_device is set)
        3. Run syncoid (with retries)
        4. Verify snapshots on remote
        5. Optionally shut down remote host

    Args:
        job: Backup job configuration

    Returns:
        JobResult with success status, details, and captured log lines
    """
    started_at = datetime.now(timezone.utc)

    # Per-job in-memory log capture
    log_lines: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
            log_lines.append(f"[{ts}] {record.levelname:7s} {record.getMessage()}")

    _handler = _ListHandler(level=logging.DEBUG)
    _root = logging.getLogger("hozo")
    _root.addHandler(_handler)

    def _log(msg: str, level: int = logging.INFO) -> None:
        logger.log(level, msg)

    try:
        return _run_job_inner(job, started_at, log_lines)
    finally:
        _root.removeHandler(_handler)


def _run_job_inner(
    job: BackupJob,
    started_at: datetime,
    log_lines: list[str],
) -> JobResult:
    # ── Step 1: Wake remote ──────────────────────────────────────────────────
    logger.info("=== Starting job: %s ===", job.name)
    logger.info("[%s] Sending WOL packet → MAC %s", job.name, job.mac_address)
    wake(job.mac_address, ip_address=job.wol_broadcast)

    # Give the machine a moment before we start hammering port 22
    time.sleep(3)

    # ── Step 2: Wait for SSH ─────────────────────────────────────────────────
    logger.info("[%s] Waiting for SSH on %s (timeout: %ds)", job.name, job.target_host, job.timeout)
    ssh_up = wait_for_ssh(job.target_host, port=job.ssh_port, timeout=job.timeout)
    if not ssh_up:
        err = f"SSH did not become available on {job.target_host} within {job.timeout}s"
        logger.error("[%s] %s", job.name, err)
        return JobResult(
            job_name=job.name,
            success=False,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            error=err,
            log_lines=log_lines,
        )

    # ── Step 2.5: Ensure backup drive is spun up ────────────────────────────
    if job.backup_device:
        logger.info(
            "[%s] Waiting for drive %s on %s to spin up…",
            job.name,
            job.backup_device,
            job.target_host,
        )
        drive_ready = wait_for_remote_drive_active(
            host=job.target_host,
            device=job.backup_device,
            ssh_user=job.ssh_user,
            ssh_port=job.ssh_port,
            ssh_key=job.ssh_key,
            timeout=job.disk_spinup_timeout,
        )
        if not drive_ready:
            err = (
                f"Drive {job.backup_device} on {job.target_host} "
                f"did not spin up within {job.disk_spinup_timeout}s"
            )
            logger.error("[%s] %s", job.name, err)
            return JobResult(
                job_name=job.name,
                success=False,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error=err,
                log_lines=log_lines,
            )

    # ── Step 3: Run syncoid (with retries) ───────────────────────────────────
    last_error: Optional[str] = None
    for attempt in range(1, job.retries + 1):
        logger.info("[%s] Syncoid attempt %d/%d", job.name, attempt, job.retries)
        try:
            _, syncoid_output = run_syncoid(
                source_dataset=job.source_dataset,
                target_host=job.target_host,
                target_dataset=job.target_dataset,
                recursive=job.recursive,
                ssh_user=job.ssh_user,
                ssh_key=job.ssh_key,
                no_privilege_elevation=job.no_privilege_elevation,
            )
            if syncoid_output:
                for line in syncoid_output.splitlines():
                    if line.strip():
                        log_lines.append(f"[syncoid] {line}")
            last_error = None
            break  # success
        except SyncoidError as exc:
            last_error = str(exc)
            if exc.stdout or exc.stderr:
                for line in (exc.stdout + exc.stderr).splitlines():
                    if line.strip():
                        log_lines.append(f"[syncoid] {line}")
            logger.warning("[%s] Attempt %d failed: %s", job.name, attempt, exc)
            if attempt < job.retries:
                logger.info("[%s] Retrying in %ds…", job.name, job.retry_delay)
                time.sleep(job.retry_delay)
        except (FileNotFoundError, Exception) as exc:
            last_error = str(exc)
            logger.warning("[%s] Attempt %d failed: %s", job.name, attempt, exc)
            if attempt < job.retries:
                logger.info("[%s] Retrying in %ds…", job.name, job.retry_delay)
                time.sleep(job.retry_delay)

    if last_error:
        # All attempts exhausted
        logger.error(
            "[%s] All %d attempts failed. Last error: %s", job.name, job.retries, last_error
        )
        _maybe_shutdown(job)
        return JobResult(
            job_name=job.name,
            success=False,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            error=last_error,
            attempts=job.retries,
            log_lines=log_lines,
        )

    # ── Step 4: Verify remote snapshots ──────────────────────────────────────
    snapshots = list_remote_snapshots(
        host=job.target_host,
        dataset=job.target_dataset,
        ssh_user=job.ssh_user,
        ssh_key=job.ssh_key,
    )
    logger.info("[%s] Remote dataset has %d snapshot(s)", job.name, len(snapshots))

    # ── Step 5: Shutdown remote ───────────────────────────────────────────────
    _maybe_shutdown(job)

    result = JobResult(
        job_name=job.name,
        success=True,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        snapshots_after=snapshots,
        attempts=attempt,  # type: ignore[possibly-undefined]
        log_lines=log_lines,
    )
    logger.info(
        "=== Job %s complete in %.1fs — %d snapshot(s) on remote ===",
        job.name,
        result.duration_seconds or 0,
        len(snapshots),
    )
    return result


def _maybe_shutdown(job: BackupJob) -> None:
    """Send a safe shutdown command to the remote host if configured."""
    if not job.shutdown_after:
        return
    logger.info("[%s] Shutting down remote host %s", job.name, job.target_host)
    try:
        exit_code, _, err = run_command(
            job.target_host,
            "shutdown -h now",
            user=job.ssh_user,
            port=job.ssh_port,
            key_path=job.ssh_key,
        )
        if exit_code not in (0, -1):  # -1 = connection dropped because machine shut down
            logger.warning(
                "[%s] Shutdown command returned %d: %s", job.name, exit_code, err.strip()
            )
    except Exception as exc:
        logger.debug(
            "[%s] Shutdown command raised (expected if machine already off): %s", job.name, exc
        )
