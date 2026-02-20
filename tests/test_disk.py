"""
Tests for drive spin-up detection and management.

Tests cover both:
- backupd.disk   — local drive state checks (runs on the backup machine)
- hozo.core.disk — SSH-based remote drive checks (runs on the orchestrator)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from backupd.disk import (
    drive_summary,
    get_drive_state,
    has_recent_io_activity,
    is_drive_active,
    spin_up_drive,
    wait_for_drive_active,
)
from hozo.core.disk import (
    is_remote_drive_active,
    remote_drive_state,
    remote_spin_up_drive,
    wait_for_remote_drive_active,
)


class TestGetDriveState:
    @patch("subprocess.run")
    def test_returns_active_idle_from_hdparm(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/dev/sda:\n  drive state is:  active/idle\n",
        )
        assert get_drive_state("/dev/sda") == "active/idle"

    @patch("subprocess.run")
    def test_returns_standby_from_hdparm(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/dev/sda:\n  drive state is:  standby\n",
        )
        assert get_drive_state("/dev/sda") == "standby"

    @patch("subprocess.run")
    def test_returns_sleeping_from_hdparm(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/dev/sda:\n  drive state is:  sleeping\n",
        )
        assert get_drive_state("/dev/sda") == "sleeping"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_falls_back_when_hdparm_missing(self, _: MagicMock, tmp_path: Path) -> None:
        # When hdparm is absent and /sys/block doesn't exist → hdparm_unavailable
        state = get_drive_state("/dev/nonexistent_drive_zzz")
        assert state == "hdparm_unavailable"

    @patch("subprocess.run")
    def test_returns_unknown_when_no_state_line(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/dev/nvme0:\n  SSD present\n")
        assert get_drive_state("/dev/nvme0") == "unknown"


class TestIsDriveActive:
    @patch("backupd.disk.get_drive_state", return_value="active/idle")
    def test_true_when_active_idle(self, _: MagicMock) -> None:
        assert is_drive_active("/dev/sda") is True

    @patch("backupd.disk.get_drive_state", return_value="standby")
    def test_false_when_standby(self, _: MagicMock) -> None:
        assert is_drive_active("/dev/sda") is False

    @patch("backupd.disk.get_drive_state", return_value="sleeping")
    def test_false_when_sleeping(self, _: MagicMock) -> None:
        assert is_drive_active("/dev/sda") is False

    @patch("backupd.disk.get_drive_state", return_value="hdparm_unavailable")
    def test_true_when_hdparm_unavailable(self, _: MagicMock) -> None:
        # When we can't determine state we optimistically assume active
        assert is_drive_active("/dev/sda") is True

    @patch("backupd.disk.get_drive_state", return_value="unknown")
    def test_true_when_unknown(self, _: MagicMock) -> None:
        assert is_drive_active("/dev/sda") is True


class TestSpinUpDrive:
    @patch("subprocess.run")
    def test_returns_true_on_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1+0 records in\n", stderr="")
        result = spin_up_drive("/dev/sda")
        assert result is True
        # Verify it's a dd sector-read, not a write
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "dd"
        assert "if=/dev/sda" in cmd
        assert "of=/dev/null" in cmd

    @patch("subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
        assert spin_up_drive("/dev/sda") is False

    @patch("subprocess.run", side_effect=Exception("device busy"))
    def test_returns_false_on_exception(self, _: MagicMock) -> None:
        assert spin_up_drive("/dev/sda") is False


class TestWaitForDriveActive:
    @patch("backupd.disk.is_drive_active", return_value=True)
    def test_returns_true_immediately_when_already_active(self, _: MagicMock) -> None:
        assert wait_for_drive_active("/dev/sda", timeout=5) is True

    @patch("backupd.disk.spin_up_drive")
    @patch("backupd.disk.is_drive_active", side_effect=[False, False, True])
    def test_spins_up_then_polls_until_active(
        self, mock_active: MagicMock, mock_spinup: MagicMock
    ) -> None:
        result = wait_for_drive_active("/dev/sda", timeout=10, poll_interval=0.01)
        assert result is True
        # spin_up_drive should be called exactly once (on first standby detection)
        mock_spinup.assert_called_once_with("/dev/sda")

    @patch("backupd.disk.spin_up_drive")
    @patch("backupd.disk.is_drive_active", return_value=False)
    def test_returns_false_on_timeout(self, mock_active: MagicMock, mock_spinup: MagicMock) -> None:
        result = wait_for_drive_active("/dev/sda", timeout=1, poll_interval=0.1)
        assert result is False

    @patch("backupd.disk.spin_up_drive")
    @patch("backupd.disk.is_drive_active", side_effect=[False, True])
    def test_does_not_spin_up_when_disabled(
        self, mock_active: MagicMock, mock_spinup: MagicMock
    ) -> None:
        result = wait_for_drive_active(
            "/dev/sda", timeout=5, poll_interval=0.01, spin_up_on_standby=False
        )
        assert result is True
        mock_spinup.assert_not_called()


class TestDriveSummary:
    @patch("backupd.disk.get_drive_state", return_value="standby")
    @patch("backupd.disk._read_io_completions", return_value=42)
    def test_summary_structure(self, _io: MagicMock, _state: MagicMock) -> None:
        summary = drive_summary("/dev/sda")
        assert summary["device"] == "/dev/sda"
        assert summary["state"] == "standby"
        assert summary["active"] is False  # standby → not active
        assert summary["io_completions"] == 42

    @patch("backupd.disk.get_drive_state", return_value="active/idle")
    @patch("backupd.disk._read_io_completions", return_value=1337)
    def test_summary_active_drive(self, _io: MagicMock, _state: MagicMock) -> None:
        summary = drive_summary("/dev/sda")
        assert summary["active"] is True


class TestHasRecentIoActivity:
    @patch("backupd.disk._read_io_completions", side_effect=[100, 105])
    def test_true_when_counter_changes(self, _: MagicMock) -> None:
        result = has_recent_io_activity("/dev/sda", probe_interval=0.01)
        assert result is True

    @patch("backupd.disk._read_io_completions", side_effect=[100, 100])
    def test_false_when_counter_static(self, _: MagicMock) -> None:
        result = has_recent_io_activity("/dev/sda", probe_interval=0.01)
        assert result is False

    @patch("backupd.disk._read_io_completions", return_value=None)
    def test_returns_none_when_sysfs_unavailable(self, _: MagicMock) -> None:
        assert has_recent_io_activity("/dev/sda", probe_interval=0.01) is None


# ─────────────────────────────────────────────────────────────────────────────
# hozo.core.disk  (remote — runs on the orchestrator, SSHes to backup machine)
# ─────────────────────────────────────────────────────────────────────────────


class TestRemoteDriveState:
    @patch("hozo.core.disk.run_command")
    def test_returns_active_idle(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "  drive state is:  active/idle\n", "")
        state = remote_drive_state("backup.local", "/dev/sda")
        assert state == "active/idle"

    @patch("hozo.core.disk.run_command")
    def test_returns_standby(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "  drive state is:  standby\n", "")
        assert remote_drive_state("backup.local", "/dev/sda") == "standby"

    @patch("hozo.core.disk.run_command")
    def test_returns_hdparm_unavailable(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "hdparm_unavailable\n", "")
        assert remote_drive_state("backup.local", "/dev/sda") == "hdparm_unavailable"

    @patch("hozo.core.disk.run_command")
    def test_returns_unknown_when_no_keyword_found(self, mock_cmd: MagicMock) -> None:
        """stdout has content but neither 'drive state is:' nor 'hdparm_unavailable' → 'unknown'."""
        mock_cmd.return_value = (0, "some other output\n", "")
        assert remote_drive_state("backup.local", "/dev/sda") == "unknown"

    @patch("hozo.core.disk.run_command", side_effect=Exception("connection refused"))
    def test_returns_unknown_on_ssh_failure(self, _: MagicMock) -> None:
        assert remote_drive_state("dead.host", "/dev/sda") == "unknown"


class TestIsRemoteDriveActive:
    @patch("hozo.core.disk.remote_drive_state", return_value="active/idle")
    def test_true_when_active(self, _: MagicMock) -> None:
        assert is_remote_drive_active("backup.local", "/dev/sda") is True

    @patch("hozo.core.disk.remote_drive_state", return_value="standby")
    def test_false_when_standby(self, _: MagicMock) -> None:
        assert is_remote_drive_active("backup.local", "/dev/sda") is False

    @patch("hozo.core.disk.remote_drive_state", return_value="unknown")
    def test_true_when_unknown(self, _: MagicMock) -> None:
        # Unknown state → assume ready (optimistic)
        assert is_remote_drive_active("backup.local", "/dev/sda") is True


class TestRemoteSpinUpDrive:
    @patch("hozo.core.disk.run_command")
    def test_returns_true_on_success(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "1+0 records in\n", "")
        assert remote_spin_up_drive("backup.local", "/dev/sda") is True
        # Verify the command sent is a dd sector-read
        cmd_str = mock_cmd.call_args[0][1]
        assert "dd" in cmd_str
        assert "/dev/sda" in cmd_str
        assert "/dev/null" in cmd_str

    @patch("hozo.core.disk.run_command")
    def test_returns_false_on_nonzero_exit(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (1, "", "permission denied")
        assert remote_spin_up_drive("backup.local", "/dev/sda") is False

    @patch("hozo.core.disk.run_command", side_effect=Exception("timeout"))
    def test_returns_false_on_exception(self, _: MagicMock) -> None:
        assert remote_spin_up_drive("backup.local", "/dev/sda") is False


class TestWaitForRemoteDriveActive:
    @patch("hozo.core.disk.is_remote_drive_active", return_value=True)
    def test_returns_true_when_already_active(self, _: MagicMock) -> None:
        assert wait_for_remote_drive_active("backup.local", "/dev/sda", timeout=5) is True

    @patch("hozo.core.disk.remote_spin_up_drive")
    @patch("hozo.core.disk.is_remote_drive_active", side_effect=[False, False, True])
    def test_spins_up_on_first_standby_then_polls(
        self, mock_active: MagicMock, mock_spinup: MagicMock
    ) -> None:
        result = wait_for_remote_drive_active(
            "backup.local", "/dev/sda", timeout=10, poll_interval=0.01
        )
        assert result is True
        mock_spinup.assert_called_once()

    @patch("hozo.core.disk.remote_spin_up_drive")
    @patch("hozo.core.disk.is_remote_drive_active", return_value=False)
    def test_returns_false_on_timeout(self, mock_active: MagicMock, mock_spinup: MagicMock) -> None:
        result = wait_for_remote_drive_active(
            "backup.local", "/dev/sda", timeout=1, poll_interval=0.1
        )
        assert result is False

    @patch("hozo.core.disk.remote_spin_up_drive")
    @patch("hozo.core.disk.is_remote_drive_active", side_effect=[False, True])
    def test_does_not_spinup_when_disabled(
        self, mock_active: MagicMock, mock_spinup: MagicMock
    ) -> None:
        result = wait_for_remote_drive_active(
            "backup.local",
            "/dev/sda",
            timeout=5,
            poll_interval=0.01,
            spin_up_on_standby=False,
        )
        assert result is True
        mock_spinup.assert_not_called()
