"""Tests for the syncoid backup wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from hozo.core.backup import SyncoidError, run_syncoid


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

        assert result is True
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

        assert result is True
        mock_run.assert_not_called()

    @patch("hozo.core.backup.subprocess.run")
    def test_target_includes_ssh_user(self, mock_run: MagicMock) -> None:
        """Target argument should be formatted as user@host:dataset."""
        mock_run.return_value = MagicMock(returncode=0)

        run_syncoid("rpool/data", "myhost", "backup/data", ssh_user="admin")

        args = mock_run.call_args[0][0]
        assert any("admin@myhost:backup/data" in a for a in args)
