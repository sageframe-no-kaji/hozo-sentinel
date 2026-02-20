"""Tests for job orchestration."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from hozo.core.job import BackupJob, JobResult, run_job, run_restore_job


def _make_job(**kwargs: object) -> BackupJob:
    defaults = dict(
        name="test",
        source_dataset="rpool/data",
        target_host="backup.local",
        target_dataset="backup/data",
        mac_address="AA:BB:CC:DD:EE:FF",
    )
    defaults.update(kwargs)  # type: ignore[arg-type]
    return BackupJob(**defaults)  # type: ignore[arg-type]


class TestBackupJob:
    """Tests for BackupJob dataclass."""

    def test_create_job_with_required_fields(self) -> None:
        """Should create job with required fields only."""
        job = _make_job()

        assert job.name == "test"
        assert job.shutdown_after is True  # default
        assert job.timeout == 120  # default

    def test_create_job_with_all_fields(self) -> None:
        """Should create job with all fields specified."""
        job = _make_job(shutdown_after=False, timeout=300)

        assert job.shutdown_after is False
        assert job.timeout == 300


class TestJobResult:
    """Tests for JobResult dataclass."""

    def test_duration_seconds_calculated(self) -> None:
        started = datetime(2024, 1, 1, 0, 0, 0)
        finished = datetime(2024, 1, 1, 0, 5, 0)
        result = JobResult(
            job_name="test",
            success=True,
            started_at=started,
            finished_at=finished,
        )
        assert result.duration_seconds == 300.0

    def test_duration_none_without_finished_at(self) -> None:
        result = JobResult(job_name="test", success=False, started_at=datetime.now(timezone.utc))
        assert result.duration_seconds is None


class TestRunJob:
    """Tests for run_job function."""

    def _mock_successful_run(self) -> tuple:
        """Return a set of patches for a fully successful job run."""
        mock_wake = MagicMock(return_value=True)
        mock_wait = MagicMock(return_value=True)
        mock_syncoid = MagicMock(return_value=(True, ""))
        mock_snapshots = MagicMock(return_value=["backup/data@2024-01-01"])
        mock_run_cmd = MagicMock(return_value=(0, "", ""))
        return mock_wake, mock_wait, mock_syncoid, mock_snapshots, mock_run_cmd

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_successful_job_returns_success(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_run_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = ["backup/data@snap1"]
        mock_run_cmd.return_value = (0, "", "")

        job = _make_job()
        result = run_job(job)

        assert result.success is True
        assert result.job_name == "test"
        assert len(result.snapshots_after) == 1

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_ssh_timeout_returns_failure(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = False  # SSH never came up

        job = _make_job()
        result = run_job(job)

        assert result.success is False
        assert result.error is not None
        assert "SSH" in result.error

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_shutdown_called_after_success(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_run_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = []
        mock_run_cmd.return_value = (0, "", "")

        job = _make_job(shutdown_after=True)
        run_job(job)

        # run_command should be called for shutdown
        mock_run_cmd.assert_called()
        shutdown_calls = [c for c in mock_run_cmd.call_args_list if "shutdown" in str(c)]
        assert len(shutdown_calls) == 1

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_no_shutdown_when_disabled(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_run_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = []
        mock_run_cmd.return_value = (0, "", "")

        job = _make_job(shutdown_after=False)
        run_job(job)

        # run_command should NOT be called (no shutdown, no snapshot commands beyond list)
        mock_run_cmd.assert_not_called()


class TestRunRestoreJob:
    """Tests for the break-glass restore job runner."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_restore_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_successful_restore_returns_success(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_restore: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = True
        mock_restore.return_value = (True, "")

        result = run_restore_job(_make_job())

        assert result.success is True
        assert result.job_name == "test"
        mock_restore.assert_called_once()

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_restore_fails_when_ssh_unavailable(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait.return_value = False

        result = run_restore_job(_make_job())

        assert result.success is False
        assert result.error is not None
        assert "SSH" in result.error

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_restore_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_restore_returns_failure_on_syncoid_error(
        self,
        mock_wake: MagicMock,
        mock_wait: MagicMock,
        mock_restore: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        from hozo.core.backup import SyncoidError

        mock_wake.return_value = True
        mock_wait.return_value = True
        mock_restore.side_effect = SyncoidError(1, "dataset not found", "")

        result = run_restore_job(_make_job())

        assert result.success is False
        assert result.error is not None
        assert "syncoid restore failed" in result.error


# ── Additional coverage ───────────────────────────────────────────────────────


class TestRunJobBackupDevice:
    """Tests for the backup_device drive-spinup branch."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_remote_drive_active")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_backup_device_drive_ready(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_drive: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_drive.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = []
        mock_cmd.return_value = (0, "", "")

        job = _make_job(backup_device="/dev/sdb", disk_spinup_timeout=60)
        result = run_job(job)

        assert result.success is True
        mock_drive.assert_called_once()

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.wait_for_remote_drive_active")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_backup_device_not_ready_returns_failure(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_drive: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_drive.return_value = False  # drive didn't spin up

        job = _make_job(backup_device="/dev/sdb", disk_spinup_timeout=60)
        result = run_job(job)

        assert result.success is False
        assert result.error is not None
        assert "/dev/sdb" in result.error or "spin up" in result.error.lower() or "Drive" in result.error


class TestRunJobGenericException:
    """Generic Exception (e.g. FileNotFoundError) in the retry loop."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job._maybe_shutdown")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_file_not_found_exhausts_retries(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_shutdown: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.side_effect = FileNotFoundError("syncoid not found in PATH")

        job = _make_job(retries=2, retry_delay=0)
        result = run_job(job)

        assert result.success is False
        assert result.error is not None
        assert mock_syncoid.call_count == 2  # retried once


class TestMaybeShutdownException:
    """_maybe_shutdown exceptions are swallowed (machine already off)."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_shutdown_exception_does_not_crash_job(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = []
        mock_cmd.side_effect = Exception("Connection reset by peer")

        job = _make_job(shutdown_after=True)
        result = run_job(job)

        # Job should still succeed even if shutdown raises
        assert result.success is True


class TestRunJobLogLinesCapture:
    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_syncoid_output_appears_in_log_lines(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.return_value = (True, "Sending snaps\nTransfer complete\n")
        mock_snapshots.return_value = []
        mock_cmd.return_value = (0, "", "")

        result = run_job(_make_job())
        combined = "\n".join(result.log_lines)
        assert "Sending snaps" in combined or "Transfer complete" in combined


class TestRestoreJobOutputCapture:
    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_restore_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_syncoid_output_in_restore_log_lines(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_restore: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_restore.return_value = (True, "Pulling snaps\nDone\n")

        result = run_restore_job(_make_job())

        assert result.success is True
        combined = "\n".join(result.log_lines)
        assert "Pulling snaps" in combined or "Done" in combined


class TestSyncoidErrorOutputCapture:
    """Cover the SyncoidError branch that includes stdout/stderr output (lines 187-195)."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job._maybe_shutdown")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_syncoid_error_output_appended_to_log_lines(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_shutdown: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        from hozo.core.backup import SyncoidError

        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.side_effect = SyncoidError(
            1,
            stderr="dataset not found",
            stdout="partial output",
        )

        job = _make_job(retries=1, retry_delay=0)
        result = run_job(job)

        assert result.success is False
        combined = "\n".join(result.log_lines)
        # The error output should appear somewhere in the log
        assert "dataset not found" in combined or "partial output" in combined

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job._maybe_shutdown")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_syncoid_error_retries_with_delay(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_shutdown: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """With retries=2, retry_delay is called between attempts."""
        from hozo.core.backup import SyncoidError

        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.side_effect = SyncoidError(1, "err")

        job = _make_job(retries=2, retry_delay=5)
        result = run_job(job)

        assert result.success is False
        # sleep(retry_delay) called between the 2 attempts
        mock_sleep.assert_any_call(5)


class TestMaybeShutdownWarning:
    """Cover the exit_code warning branch in _maybe_shutdown (line 263)."""

    @patch("hozo.core.job.time.sleep")
    @patch("hozo.core.job.run_command")
    @patch("hozo.core.job.list_remote_snapshots")
    @patch("hozo.core.job.run_syncoid")
    @patch("hozo.core.job.wait_for_ssh")
    @patch("hozo.core.job.wake")
    def test_nonzero_shutdown_exit_code_does_not_crash(
        self,
        mock_wake: MagicMock,
        mock_wait_ssh: MagicMock,
        mock_syncoid: MagicMock,
        mock_snapshots: MagicMock,
        mock_cmd: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """If shutdown command returns exit code 1, we just log a warning."""
        mock_wake.return_value = True
        mock_wait_ssh.return_value = True
        mock_syncoid.return_value = (True, "")
        mock_snapshots.return_value = []
        # Return exit_code=1 (not 0 or -1) to trigger the warning branch
        mock_cmd.return_value = (1, "", "permission denied")

        job = _make_job(shutdown_after=True)
        result = run_job(job)

        # Job should still succeed even with nonzero shutdown exit code
        assert result.success is True
