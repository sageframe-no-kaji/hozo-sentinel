"""Tests for the Hōzō CLI."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from hozo.cli import main
from hozo.core.job import JobResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_config(path: Path, **job_overrides: object) -> None:
    """Write a minimal valid config with one job named 'weekly'."""
    job: dict = {
        "name": "weekly",
        "source": "rpool/data",
        "target_host": "backup.local",
        "target_dataset": "backup/data",
        "mac_address": "AA:BB:CC:DD:EE:FF",
    }
    job.update(job_overrides)
    path.write_text(yaml.dump({"jobs": [job]}))


def _fail_result() -> JobResult:
    return JobResult(
        job_name="weekly",
        success=False,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        error="syncoid failed",
    )


def _ok_result() -> JobResult:
    return JobResult(
        job_name="weekly",
        success=True,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        snapshots_after=["backup/data@snap1"],
    )


# ── _load_cfg error paths ──────────────────────────────────────────────────────


class TestLoadCfgErrors:
    """_load_cfg calls sys.exit(1) for bad configs; CliRunner captures that."""

    def test_missing_config_exits_1(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["--config", str(tmp_path / "nope.yaml"), "jobs", "list"]
        )
        assert result.exit_code == 1

    def test_empty_config_exits_1(self, tmp_path: Path) -> None:
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        result = CliRunner().invoke(main, ["--config", str(cfg), "jobs", "list"])
        assert result.exit_code == 1

    def test_validation_error_exits_1(self, tmp_path: Path) -> None:
        """A job missing required fields triggers validate_config errors → exit 1."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(yaml.dump({"jobs": [{"name": "x"}]}))
        result = CliRunner().invoke(main, ["--config", str(cfg), "jobs", "list"])
        assert result.exit_code == 1


# ── jobs list ─────────────────────────────────────────────────────────────────


class TestJobsList:
    def test_shows_configured_job_name(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "jobs", "list"])
        assert result.exit_code == 0
        assert "weekly" in result.output

    def test_shows_source_and_host(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "jobs", "list"])
        assert "rpool/data" in result.output
        assert "backup.local" in result.output

    @patch("hozo.cli._load_cfg", return_value=({"jobs": []}, []))
    def test_no_jobs_prints_message(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """validate_config requires non-empty jobs, so we mock _load_cfg to reach the branch."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("placeholder: true")
        result = CliRunner().invoke(main, ["--config", str(cfg), "jobs", "list"])
        assert result.exit_code == 0
        assert "No jobs configured." in result.output


# ── jobs run ──────────────────────────────────────────────────────────────────


class TestJobsRun:
    @patch("hozo.notifications.notify.send_notification")
    @patch("hozo.core.job.run_job")
    def test_success_exits_0_and_prints_checkmark(
        self, mock_run: MagicMock, mock_notify: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _ok_result()
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "jobs", "run", "weekly"]
        )
        assert result.exit_code == 0
        assert "✓" in result.output or "completed" in result.output.lower()

    @patch("hozo.notifications.notify.send_notification")
    @patch("hozo.core.job.run_job")
    def test_failure_exits_2(
        self, mock_run: MagicMock, mock_notify: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _fail_result()
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "jobs", "run", "weekly"]
        )
        assert result.exit_code == 2

    def test_unknown_job_exits_1(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "jobs", "run", "nonexistent"]
        )
        assert result.exit_code == 1

    @patch("hozo.notifications.notify.send_notification")
    @patch("hozo.core.job.run_job")
    def test_notification_called_after_run(
        self, mock_run: MagicMock, mock_notify: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _ok_result()
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        CliRunner().invoke(main, ["--config", str(cfg), "jobs", "run", "weekly"])
        mock_notify.assert_called_once()


# ── status ────────────────────────────────────────────────────────────────────


class TestStatus:
    @patch("hozo.cli._load_cfg", return_value=({"jobs": []}, []))
    def test_no_jobs_prints_message(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """validate_config requires non-empty jobs, so we mock _load_cfg to reach the branch."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("placeholder: true")
        result = CliRunner().invoke(main, ["--config", str(cfg), "status"])
        assert result.exit_code == 0
        assert "No jobs configured." in result.output

    @patch("hozo.core.ssh.run_command", return_value=(0, "loads\n", ""))
    @patch("hozo.core.ssh.wait_for_ssh", return_value=True)
    def test_ssh_reachable_runs_commands(
        self, mock_wait: MagicMock, mock_cmd: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "status"])
        assert result.exit_code == 0
        assert mock_cmd.called

    @patch("hozo.core.ssh.wait_for_ssh", return_value=False)
    def test_ssh_unreachable_prints_error(
        self, mock_wait: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "status"])
        assert result.exit_code == 0
        assert "unreachable" in result.output.lower()

    @patch("hozo.core.ssh.run_command", return_value=(0, "sysinfo\n", ""))
    @patch("hozo.core.ssh.wait_for_ssh", return_value=True)
    def test_status_with_named_job(
        self, mock_wait: MagicMock, mock_cmd: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "status", "--job", "weekly"]
        )
        assert result.exit_code == 0

    def test_status_unknown_job_exits_1(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "status", "--job", "ghost"]
        )
        assert result.exit_code == 1


# ── wake ─────────────────────────────────────────────────────────────────────


class TestWake:
    @patch("hozo.core.wol.wake")
    def test_wake_valid_job_calls_wol(self, mock_wake: MagicMock, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "wake", "weekly"])
        assert result.exit_code == 0
        mock_wake.assert_called_once()
        assert "AA:BB:CC:DD:EE:FF" in result.output

    def test_wake_unknown_job_exits_1(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(main, ["--config", str(cfg), "wake", "ghost"])
        assert result.exit_code == 1


# ── shutdown ──────────────────────────────────────────────────────────────────


class TestShutdown:
    @patch("hozo.core.ssh.run_command", return_value=(0, "", ""))
    def test_shutdown_valid_job_sends_command(
        self, mock_cmd: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "shutdown", "weekly"]
        )
        assert result.exit_code == 0
        assert mock_cmd.called
        assert "shutdown" in result.output.lower()

    def test_shutdown_unknown_job_exits_1(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "shutdown", "ghost"]
        )
        assert result.exit_code == 1

    @patch(
        "hozo.core.ssh.run_command",
        side_effect=Exception("Connection reset"),
    )
    def test_shutdown_exception_is_graceful(
        self, mock_cmd: MagicMock, tmp_path: Path
    ) -> None:
        """SSH raising (machine already off) should be caught and printed, not crash."""
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        result = CliRunner().invoke(
            main, ["--config", str(cfg), "shutdown", "weekly"]
        )
        assert result.exit_code == 0
        assert "Connection reset" in result.output or "shut down" in result.output.lower()


# ── serve ─────────────────────────────────────────────────────────────────────


class TestServe:
    @patch("uvicorn.run")
    @patch("hozo.api.routes.create_app")
    def test_serve_starts_uvicorn(
        self, mock_create: MagicMock, mock_uvicorn: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        mock_create.return_value = MagicMock()
        result = CliRunner().invoke(
            main,
            ["--config", str(cfg), "serve", "--host", "127.0.0.1", "--port", "9999"],
        )
        assert result.exit_code == 0
        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs[1]["host"] == "127.0.0.1"
        assert call_kwargs[1]["port"] == 9999

    @patch("uvicorn.run")
    @patch("hozo.api.routes.create_app")
    def test_serve_prints_startup_message(
        self, mock_create: MagicMock, mock_uvicorn: MagicMock, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.yaml"
        _write_config(cfg)
        mock_create.return_value = MagicMock()
        result = CliRunner().invoke(main, ["--config", str(cfg), "serve"])
        assert "Starting" in result.output or "hozo" in result.output.lower()
