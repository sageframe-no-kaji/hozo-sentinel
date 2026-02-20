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
