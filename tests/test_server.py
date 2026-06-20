"""
Tests for backupd.server — the remote agent's HTTP API.

Covers the bearer-token auth gate (fail-closed when unconfigured), device-name
validation, and each endpoint's happy path with its system calls mocked.
"""

from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backupd import server
from backupd.server import app

TOKEN = "test-secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("BACKUPD_TOKEN", TOKEN)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_token_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("BACKUPD_TOKEN", raising=False)
    with TestClient(app) as c:
        yield c


class TestPing:
    def test_ping_open_without_token(self, no_token_client: TestClient) -> None:
        # /ping is a liveness probe and must work with no token configured.
        with patch("backupd.server.get_uptime", return_value=10.0):
            resp = no_token_client.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "uptime": 10.0}


class TestAuthGate:
    def test_status_503_when_token_unconfigured(self, no_token_client: TestClient) -> None:
        assert no_token_client.get("/status").status_code == 503

    def test_status_401_with_wrong_token(self, client: TestClient) -> None:
        resp = client.get("/status", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_status_401_without_header(self, client: TestClient) -> None:
        assert client.get("/status").status_code == 401

    def test_shutdown_requires_token(self, no_token_client: TestClient) -> None:
        assert no_token_client.post("/shutdown").status_code == 503


class TestStatus:
    def test_status_payload(self, client: TestClient) -> None:
        with (
            patch("backupd.server.list_pools", return_value=["tank"]),
            patch("backupd.server.get_pool_status", return_value={"tank": "ONLINE"}),
            patch("backupd.server.get_uptime", return_value=42.0),
        ):
            resp = client.get("/status", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {
            "uptime_seconds": 42.0,
            "pools": ["tank"],
            "pool_states": {"tank": "ONLINE"},
        }


class TestShutdown:
    def test_returns_scheduled_and_spawns_thread(self, client: TestClient) -> None:
        with patch("backupd.server.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            resp = client.post("/shutdown", headers=AUTH)
        # 202 Accepted — the work is scheduled, not completed; failures are
        # only visible in the backupd log because the response has been sent.
        assert resp.status_code == 202
        assert resp.json()["status"] == "scheduled"
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

    def test_logs_error_when_safe_shutdown_fails(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """safe_shutdown returning False (e.g. shutdown binary missing) must
        surface in the backupd log since the HTTP response has already gone."""
        import logging

        with patch("backupd.server.safe_shutdown", return_value=False):
            with caplog.at_level(logging.ERROR, logger="backupd.server"):
                resp = client.post("/shutdown", headers=AUTH)
                # Give the daemon thread a moment to run.
                import time

                for _ in range(20):
                    if any("safe_shutdown returned False" in r.message for r in caplog.records):
                        break
                    time.sleep(0.05)
        assert resp.status_code == 202
        assert any("safe_shutdown returned False" in r.message for r in caplog.records)


class TestDiskStatus:
    def test_disk_summary(self, client: TestClient) -> None:
        summary = {"device": "/dev/sda", "state": "standby", "active": False, "io_completions": 0}
        with patch("backupd.server.drive_summary", return_value=summary) as mock_summary:
            resp = client.get("/disk/sda", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == summary
        mock_summary.assert_called_once_with("/dev/sda")

    def test_rejects_bad_device(self, client: TestClient) -> None:
        # Metacharacters must be rejected before reaching the /dev path.
        assert client.get("/disk/sda;rm", headers=AUTH).status_code == 400


class TestDiskSpinup:
    def test_spinup_ready_true(self, client: TestClient) -> None:
        with patch("backupd.server.wait_for_drive_active", return_value=True) as mock_wait:
            resp = client.post("/disk/sda/spinup", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"device": "/dev/sda", "ready": True}
        mock_wait.assert_called_once_with("/dev/sda", timeout=60, spin_up_on_standby=True)

    def test_spinup_ready_false(self, client: TestClient) -> None:
        with patch("backupd.server.wait_for_drive_active", return_value=False):
            resp = client.post("/disk/sda/spinup", headers=AUTH)
        assert resp.json()["ready"] is False

    def test_rejects_bad_device(self, client: TestClient) -> None:
        assert client.post("/disk/sda;rm/spinup", headers=AUTH).status_code == 400


class TestRun:
    def test_invokes_uvicorn_with_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKUPD_TOKEN", TOKEN)
        monkeypatch.setenv("BACKUPD_HOST", "127.0.0.1")
        monkeypatch.setenv("BACKUPD_PORT", "1234")
        with patch("backupd.server.uvicorn.run") as mock_run:
            server.run()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["host"] == "127.0.0.1"
        assert mock_run.call_args.kwargs["port"] == 1234

    def test_explicit_args_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKUPD_TOKEN", raising=False)
        with patch("backupd.server.uvicorn.run") as mock_run:
            server.run(host="10.0.0.5", port=9000)
        assert mock_run.call_args.kwargs["host"] == "10.0.0.5"
        assert mock_run.call_args.kwargs["port"] == 9000
