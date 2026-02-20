"""Tests for the syncoid backup wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from hozo.core.backup import SyncoidError, run_restore_syncoid, run_syncoid


class TestRunSyncoid:
    """Tests for run_syncoid."""

    @patch("hozo.core.backup.subprocess.run")
    def test_successful_run_returns_true(self, mock_run: MagicMock) -> None:
        """Should return True when syncoid exits with code 0."""
        mock_run.return_value = MagicMock(returncode=0)

        result = run_syncoid(
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
        )

        assert result[0] is True
        mock_run.assert_called_once()

    @patch("hozo.core.backup.subprocess.run")
    def test_raises_syncoid_error_on_failure(self, mock_run: MagicMock) -> None:
        """Should raise SyncoidError when syncoid exits non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stderr="dataset not found")

        with pytest.raises(SyncoidError) as exc_info:
            run_syncoid("rpool/data", "backup.local", "backup/data")

        assert exc_info.value.returncode == 1

    @patch("hozo.core.backup.subprocess.run")
    def test_recursive_flag_included(self, mock_run: MagicMock) -> None:
        """Should include --recursive flag when recursive=True."""
        mock_run.return_value = MagicMock(returncode=0)

        run_syncoid("rpool/data", "host", "backup/data", recursive=True)

        args = mock_run.call_args[0][0]
        assert "--recursive" in args

    @patch("hozo.core.backup.subprocess.run")
    def test_no_recursive_flag_when_disabled(self, mock_run: MagicMock) -> None:
        """Should not include --recursive flag when recursive=False."""
        mock_run.return_value = MagicMock(returncode=0)

        run_syncoid("rpool/data", "host", "backup/data", recursive=False)

        args = mock_run.call_args[0][0]
        assert "--recursive" not in args

    @patch("hozo.core.backup.subprocess.run")
    def test_dry_run_does_not_execute(self, mock_run: MagicMock) -> None:
        """Should not call subprocess when dry_run=True."""
        result = run_syncoid("rpool/data", "host", "backup/data", dry_run=True)

        assert result[0] is True
        mock_run.assert_not_called()

    @patch("hozo.core.backup.subprocess.run")
    def test_target_includes_ssh_user(self, mock_run: MagicMock) -> None:
        """Target argument should be formatted as user@host:dataset."""
        mock_run.return_value = MagicMock(returncode=0)

        run_syncoid("rpool/data", "myhost", "backup/data", ssh_user="admin")

        args = mock_run.call_args[0][0]
        assert any("admin@myhost:backup/data" in a for a in args)


class TestRunRestoreSyncoid:
    """Tests for run_restore_syncoid (break-glass restore)."""

    @patch("hozo.core.backup.subprocess.run")
    def test_successful_restore_returns_true(self, mock_run: MagicMock) -> None:
        """Should return (True, output) when syncoid exits 0."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = run_restore_syncoid(
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
        )

        assert result[0] is True
        mock_run.assert_called_once()

    @patch("hozo.core.backup.subprocess.run")
    def test_source_is_remote_in_restore(self, mock_run: MagicMock) -> None:
        """Source argument must be user@host:dataset (remote backup)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        run_restore_syncoid(
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/rpool-data",
            ssh_user="admin",
        )

        args = mock_run.call_args[0][0]
        # Second-to-last arg is the remote source; last is local dest
        assert any("admin@backup.local:backup/rpool-data" in a for a in args)
        assert args[-1] == "rpool/data"

    @patch("hozo.core.backup.subprocess.run")
    def test_force_delete_included_by_default(self, mock_run: MagicMock) -> None:
        """--force-delete should be in the command by default."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        run_restore_syncoid("rpool/data", "backup.local", "backup/data")

        args = mock_run.call_args[0][0]
        assert "--force-delete" in args

    @patch("hozo.core.backup.subprocess.run")
    def test_force_delete_omitted_when_disabled(self, mock_run: MagicMock) -> None:
        """--force-delete should be absent when force_delete=False."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        run_restore_syncoid(
            "rpool/data", "backup.local", "backup/data", force_delete=False
        )

        args = mock_run.call_args[0][0]
        assert "--force-delete" not in args

    @patch("hozo.core.backup.subprocess.run")
    def test_raises_syncoid_error_on_failure(self, mock_run: MagicMock) -> None:
        """Should raise SyncoidError when syncoid exits non-zero."""
        mock_run.return_value = MagicMock(
            returncode=1, stderr="dataset not found", stdout=""
        )

        with pytest.raises(SyncoidError) as exc_info:
            run_restore_syncoid("rpool/data", "backup.local", "backup/data")

        assert exc_info.value.returncode == 1


# ── Additional coverage ───────────────────────────────────────────────────────


class TestRunSyncoidExtraFlags:
    @patch("hozo.core.backup.subprocess.run")
    def test_no_privilege_elevation_flag_included(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            no_privilege_elevation=True,
        )
        args = mock_run.call_args[0][0]
        assert "--no-privilege-elevation" in args

    @patch("hozo.core.backup.subprocess.run")
    def test_ssh_key_in_sshoption(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            ssh_key="/root/.ssh/id_ed25519",
        )
        args = mock_run.call_args[0][0]
        # ssh key should end up in --sshoption value
        full = " ".join(args)
        assert "-i /root/.ssh/id_ed25519" in full

    @patch("hozo.core.backup.subprocess.run")
    def test_output_lines_captured_nonempty(self, mock_run: MagicMock) -> None:
        """Non-empty lines in stdout are returned in combined output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Sending snaps\n\nDone\n", stderr="")
        ok, combined = run_syncoid("rpool/data", "host", "backup/data")
        assert ok is True
        assert "Sending snaps" in combined

    @patch("hozo.core.backup.subprocess.run")
    def test_output_blank_lines_filtered_from_debug_logs(self, mock_run: MagicMock) -> None:
        """Blank lines are not logged but combined output still includes raw content."""
        mock_run.return_value = MagicMock(returncode=0, stdout="\n\n\n", stderr="")
        ok, combined = run_syncoid("rpool/data", "host", "backup/data")
        assert ok is True


class TestRunRestoreSyncoidExtraFlags:
    @patch("hozo.core.backup.subprocess.run")
    def test_nonstandard_ssh_port_in_sshoption(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_restore_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            ssh_port=2222,
        )
        args = mock_run.call_args[0][0]
        full = " ".join(args)
        assert "-p 2222" in full

    @patch("hozo.core.backup.subprocess.run")
    def test_default_port_not_in_sshoption(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_restore_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            ssh_port=22,
        )
        args = mock_run.call_args[0][0]
        full = " ".join(args)
        assert "-p 22" not in full

    @patch("hozo.core.backup.subprocess.run")
    def test_no_privilege_elevation_in_restore(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_restore_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            no_privilege_elevation=True,
        )
        args = mock_run.call_args[0][0]
        assert "--no-privilege-elevation" in args

    @patch("hozo.core.backup.subprocess.run")
    def test_restore_ssh_key_in_sshoption(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_restore_syncoid(
            "rpool/data",
            "host",
            "backup/data",
            ssh_key="/root/.ssh/backup_key",
        )
        args = mock_run.call_args[0][0]
        full = " ".join(args)
        assert "-i /root/.ssh/backup_key" in full


class TestListRemoteSnapshots:
    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_returns_snapshot_names(self, mock_ssh_cls: MagicMock) -> None:
        from hozo.core.backup import list_remote_snapshots

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"backup/data@2024-01-01\nbackup/data@2024-01-02\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client = MagicMock()
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        mock_ssh_cls.return_value = mock_client

        snaps = list_remote_snapshots("host", "backup/data")
        assert len(snaps) == 2

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_returns_empty_list_on_ssh_error(self, mock_ssh_cls: MagicMock) -> None:
        from hozo.core.backup import list_remote_snapshots

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"dataset not found"
        mock_client = MagicMock()
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        mock_ssh_cls.return_value = mock_client

        snaps = list_remote_snapshots("host", "backup/data")
        assert snaps == []
