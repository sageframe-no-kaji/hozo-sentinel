"""Tests for job orchestration."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from hozo.core.job import BackupJob, JobResult, run_job


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
