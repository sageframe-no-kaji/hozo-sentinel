"""
Tests for backupd.system — uptime + safe shutdown on the backup machine.

Nothing real is shut down: subprocess.run, shutil.which, time.sleep, and the
ZFS helpers are all mocked.
"""

from unittest.mock import MagicMock, patch

from backupd.system import get_uptime, safe_shutdown


class TestGetUptime:
    @patch("backupd.system.Path")
    def test_reads_proc_uptime(self, mock_path: MagicMock) -> None:
        inst = mock_path.return_value
        inst.exists.return_value = True
        inst.read_text.return_value = "12345.67 89012.34"
        assert get_uptime() == 12345.67

    @patch("backupd.system.Path")
    def test_returns_zero_when_missing(self, mock_path: MagicMock) -> None:
        mock_path.return_value.exists.return_value = False
        assert get_uptime() == 0.0

    @patch("backupd.system.Path")
    def test_returns_zero_on_parse_error(self, mock_path: MagicMock) -> None:
        inst = mock_path.return_value
        inst.exists.return_value = True
        inst.read_text.return_value = "not-a-number"
        assert get_uptime() == 0.0


class TestSafeShutdown:
    @patch("backupd.system.subprocess.run")
    @patch("backupd.system.time.sleep")
    @patch("backupd.system.export_pool")
    @patch("backupd.system.list_pools", return_value=["tank", "backup"])
    @patch("backupd.system.shutil.which", return_value="/sbin/shutdown")
    def test_exports_pools_then_shuts_down(
        self,
        _which: MagicMock,
        _list: MagicMock,
        mock_export: MagicMock,
        _sleep: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        assert safe_shutdown(export_pools=True, delay_seconds=0) is True
        assert mock_export.call_count == 2
        assert mock_run.call_args[0][0] == ["/sbin/shutdown", "-h", "now"]

    @patch("backupd.system.subprocess.run")
    @patch("backupd.system.time.sleep")
    @patch("backupd.system.export_pool")
    @patch("backupd.system.list_pools", return_value=["tank"])
    @patch("backupd.system.shutil.which", return_value="/sbin/shutdown")
    def test_skips_export_when_disabled(
        self,
        _which: MagicMock,
        _list: MagicMock,
        mock_export: MagicMock,
        _sleep: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        assert safe_shutdown(export_pools=False, delay_seconds=0) is True
        mock_export.assert_not_called()
        mock_run.assert_called_once()

    @patch("backupd.system.subprocess.run")
    @patch("backupd.system.export_pool")
    @patch("backupd.system.list_pools", return_value=["tank"])
    @patch("backupd.system.shutil.which", return_value=None)
    def test_aborts_without_export_when_shutdown_missing(
        self,
        _which: MagicMock,
        _list: MagicMock,
        mock_export: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        # If `shutdown` isn't on PATH we must NOT export pools and must report
        # failure — exporting then failing to power off breaks the next backup.
        assert safe_shutdown(export_pools=True) is False
        mock_export.assert_not_called()
        mock_run.assert_not_called()

    @patch("backupd.system.subprocess.run", side_effect=Exception("denied"))
    @patch("backupd.system.time.sleep")
    @patch("backupd.system.export_pool")
    @patch("backupd.system.list_pools", return_value=[])
    @patch("backupd.system.shutil.which", return_value="/sbin/shutdown")
    def test_returns_false_when_shutdown_raises(
        self,
        _which: MagicMock,
        _list: MagicMock,
        _export: MagicMock,
        _sleep: MagicMock,
        _run: MagicMock,
    ) -> None:
        assert safe_shutdown(export_pools=True, delay_seconds=0) is False

    @patch("backupd.system.subprocess.run")
    @patch("backupd.system.time.sleep")
    @patch("backupd.system.list_pools", return_value=[])
    @patch("backupd.system.shutil.which", return_value="/sbin/shutdown")
    def test_respects_delay(
        self,
        _which: MagicMock,
        _list: MagicMock,
        mock_sleep: MagicMock,
        _run: MagicMock,
    ) -> None:
        safe_shutdown(export_pools=False, delay_seconds=5)
        mock_sleep.assert_called_once_with(5)
