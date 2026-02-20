"""Tests for SSH connectivity and remote command execution."""

from unittest.mock import MagicMock, patch

from hozo.core.ssh import run_command, wait_for_ssh


class TestWaitForSsh:
    """Tests for wait_for_ssh."""

    @patch("hozo.core.ssh.socket.create_connection")
    def test_returns_true_when_reachable(self, mock_conn: MagicMock) -> None:
        """Should return True immediately when SSH port is reachable."""
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = wait_for_ssh("backup.local", timeout=10)

        assert result is True
        mock_conn.assert_called_once()

    @patch("hozo.core.ssh.socket.create_connection", side_effect=OSError)
    @patch("hozo.core.ssh.time.sleep")
    def test_returns_false_on_timeout(self, mock_sleep: MagicMock, mock_conn: MagicMock) -> None:
        """Should return False when SSH never becomes available."""
        # timeout=1 with poll_interval=5 → loop exits immediately after one attempt
        result = wait_for_ssh("unreachable.host", timeout=1, poll_interval=5.0)
        assert result is False

    @patch("hozo.core.ssh.socket.create_connection")
    def test_succeeds_on_second_attempt(self, mock_conn: MagicMock) -> None:
        """Should pass even if first attempt fails."""
        mock_conn.side_effect = [
            OSError,
            MagicMock(__enter__=lambda s: s, __exit__=MagicMock(return_value=False)),
        ]

        with patch("hozo.core.ssh.time.sleep"):
            result = wait_for_ssh("backup.local", timeout=30, poll_interval=1.0)

        assert result is True


class TestRunCommand:
    """Tests for run_command via paramiko."""

    def _make_mock_client(
        self, stdout_data: str = "", stderr_data: str = "", exit_code: int = 0
    ) -> MagicMock:
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = stdout_data.encode()
        mock_stdout.channel.recv_exit_status.return_value = exit_code

        mock_stderr = MagicMock()
        mock_stderr.read.return_value = stderr_data.encode()

        mock_client = MagicMock()
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        return mock_client

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_returns_tuple_on_success(self, mock_ssh_cls: MagicMock) -> None:
        mock_client = self._make_mock_client(stdout_data="hello\n", exit_code=0)
        mock_ssh_cls.return_value = mock_client

        ec, stdout, stderr = run_command("host", "echo hello")

        assert ec == 0
        assert "hello" in stdout

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_returns_nonzero_exit_code(self, mock_ssh_cls: MagicMock) -> None:
        mock_client = self._make_mock_client(stderr_data="not found", exit_code=127)
        mock_ssh_cls.return_value = mock_client

        ec, stdout, stderr = run_command("host", "badcmd")

        assert ec == 127
        assert "not found" in stderr

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_client_closed_after_command(self, mock_ssh_cls: MagicMock) -> None:
        mock_client = self._make_mock_client()
        mock_ssh_cls.return_value = mock_client

        run_command("host", "uptime")

        mock_client.close.assert_called_once()


class TestRunCommandCredentials:
    """Cover key_path and password branches."""

    def _make_mock_client(
        self, stdout_data: str = "", stderr_data: str = "", exit_code: int = 0
    ) -> MagicMock:
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = stdout_data.encode()
        mock_stdout.channel.recv_exit_status.return_value = exit_code
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = stderr_data.encode()
        mock_client = MagicMock()
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        return mock_client

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_key_path_passed_to_connect(self, mock_ssh_cls: MagicMock) -> None:
        mock_client = self._make_mock_client()
        mock_ssh_cls.return_value = mock_client

        run_command("host", "cmd", key_path="~/.ssh/id_ed25519")

        call_kwargs = mock_client.connect.call_args[1]
        assert "key_filename" in call_kwargs
        assert "id_ed25519" in call_kwargs["key_filename"]

    @patch("hozo.core.ssh.paramiko.SSHClient")
    def test_password_passed_to_connect(self, mock_ssh_cls: MagicMock) -> None:
        mock_client = self._make_mock_client()
        mock_ssh_cls.return_value = mock_client

        run_command("host", "cmd", password="hunter2")

        call_kwargs = mock_client.connect.call_args[1]
        assert call_kwargs.get("password") == "hunter2"
