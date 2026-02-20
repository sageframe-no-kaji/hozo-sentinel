"""Tests for the FastAPI web API."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient


def _write_config(tmp_path: Path) -> Path:
    config = {
        "jobs": [
            {
                "name": "weekly",
                "source": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "schedule": "weekly Sunday 03:00",
            }
        ]
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(config))
    return p


@pytest.fixture()
def client(tmp_path: Path):
    from hozo.api.routes import create_app

    config_path = _write_config(tmp_path)

    # Patch HozoScheduler at the source so no real background threads are spawned
    with (
        patch("hozo.scheduler.runner.HozoScheduler.start"),
        patch("hozo.scheduler.runner.HozoScheduler.stop"),
        patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
    ):
        app = create_app(config_path=str(config_path))
        yield TestClient(app)


class TestDashboard:
    def test_root_returns_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Hōzō" in resp.text or "weekly" in resp.text


class TestStatusEndpoint:
    def test_status_returns_json(self, client: TestClient) -> None:
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "weekly"

    def test_status_has_scheduler_running_field(self, client: TestClient) -> None:
        resp = client.get("/status")
        assert "scheduler_running" in resp.json()


class TestWakeEndpoint:
    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_valid_job(self, mock_send: MagicMock, client: TestClient) -> None:
        resp = client.post("/wake", json={"job_name": "weekly"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "wol_sent"

    def test_wake_unknown_job(self, client: TestClient) -> None:
        resp = client.post("/wake", json={"job_name": "nonexistent"})
        assert resp.status_code == 404


class TestRunBackupEndpoint:
    @patch("hozo.core.job.run_job")
    def test_run_backup_valid_job(self, mock_run: MagicMock, client: TestClient) -> None:
        resp = client.post("/run_backup", json={"job_name": "weekly"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_run_backup_unknown_job(self, client: TestClient) -> None:
        resp = client.post("/run_backup", json={"job_name": "ghost"})
        assert resp.status_code == 404


class TestShutdownEndpoint:
    @patch("hozo.core.ssh.run_command")
    def test_shutdown_valid_job(self, mock_cmd: MagicMock, client: TestClient) -> None:
        resp = client.post("/shutdown", json={"job_name": "weekly"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "shutdown_sent"

    def test_shutdown_unknown_job(self, client: TestClient) -> None:
        resp = client.post("/shutdown", json={"job_name": "nobody"})
        assert resp.status_code == 404


class TestResultsEndpoint:
    def test_no_result_returns_404(self, client: TestClient) -> None:
        resp = client.get("/results/weekly")
        assert resp.status_code == 404


class TestSettingsRoutes:
    def test_get_settings_returns_html(self, client: TestClient) -> None:
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Settings" in resp.text

    def test_post_settings_redirects(self, client: TestClient) -> None:
        resp = client.post(
            "/settings",
            data={"ssh_timeout": "90", "ssh_user": "backup"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/settings")

    def test_post_settings_updates_state(self, client: TestClient) -> None:
        client.post("/settings", data={"ssh_timeout": "75", "ssh_user": "ops"})
        assert client.app.state.settings["ssh_timeout"] == 75
        assert client.app.state.settings["ssh_user"] == "ops"


class TestJobCRUD:
    def test_get_new_job_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/new")
        assert resp.status_code == 200
        assert "New Job" in resp.text

    def test_create_job(self, client: TestClient) -> None:
        before = len(client.app.state.jobs)
        resp = client.post(
            "/jobs/new",
            data={
                "name": "nightly",
                "source_dataset": "rpool/critical",
                "target_host": "backup.local",
                "target_dataset": "backup/critical",
                "mac_address": "11:22:33:44:55:66",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert len(client.app.state.jobs) == before + 1
        names = [j.name for j in client.app.state.jobs]
        assert "nightly" in names

    def test_create_duplicate_name_redirects_with_error(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "weekly",  # already exists in fixture
                "source_dataset": "rpool/x",
                "target_host": "h",
                "target_dataset": "b/x",
                "mac_address": "AA:BB:CC:DD:EE:FF",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=name" in resp.headers["location"]

    def test_get_edit_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/edit")
        assert resp.status_code == 200
        assert "weekly" in resp.text

    def test_edit_nonexistent_redirects(self, client: TestClient) -> None:
        resp = client.get("/jobs/ghost/edit", follow_redirects=False)
        assert resp.status_code == 303

    def test_edit_updates_job(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/weekly/edit",
            data={
                "name": "weekly",
                "source_dataset": "rpool/updated",
                "target_host": "backup2.local",
                "target_dataset": "backup/updated",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "ssh_user": "root",
                "ssh_port": "22",
                "retries": "3",
                "retry_delay": "60",
                "ssh_timeout": "120",
                "wol_broadcast": "255.255.255.255",
                "disk_spinup_timeout": "90",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        job = next(j for j in client.app.state.jobs if j.name == "weekly")
        assert job.source_dataset == "rpool/updated"

    def test_delete_removes_job(self, client: TestClient) -> None:
        before = len(client.app.state.jobs)
        resp = client.post("/jobs/weekly/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert len(client.app.state.jobs) == before - 1
        assert all(j.name != "weekly" for j in client.app.state.jobs)


class TestAuthPagesBootstrap:
    """Auth pages in bootstrap mode (no credentials registered)."""

    def test_login_page_shows_register_link(self, client: TestClient) -> None:
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        assert "/auth/register" in resp.text

    def test_register_page_returns_html(self, client: TestClient) -> None:
        resp = client.get("/auth/register")
        assert resp.status_code == 200
        assert "Register" in resp.text

    def test_devices_page_returns_html(self, client: TestClient) -> None:
        resp = client.get("/auth/devices")
        assert resp.status_code == 200

    def test_logout_clears_cookie(self, client: TestClient) -> None:
        resp = client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 302
