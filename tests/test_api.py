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


# ── Additional route coverage ─────────────────────────────────────────────────


class TestJobLogRoutes:
    """Log viewer routes (job_log.html + log_lines partial)."""

    def test_get_job_log_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/log")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_get_job_log_lines_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/log/lines")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_get_job_log_with_result(self, client: TestClient) -> None:
        from datetime import datetime, timezone

        from hozo.core.job import JobResult

        client.app.state.last_results["weekly"] = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
            log_lines=["[03:00:00] INFO    job started"],
        )
        resp = client.get("/jobs/weekly/log")
        assert resp.status_code == 200

    def test_get_job_log_lines_with_result(self, client: TestClient) -> None:
        from datetime import datetime, timezone

        from hozo.core.job import JobResult

        client.app.state.last_results["weekly"] = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
            log_lines=["line one", "line two"],
        )
        resp = client.get("/jobs/weekly/log/lines")
        assert resp.status_code == 200


class TestRestoreRoutes:
    """Break-glass restore routes."""

    def test_get_restore_confirm_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/restore")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_get_restore_confirm_unknown_job_returns_404(self, client: TestClient) -> None:
        resp = client.get("/jobs/ghost/restore")
        assert resp.status_code == 404

    def test_post_restore_wrong_name_shows_error(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/weekly/restore",
            data={"confirm_name": "WRONG"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "error" in resp.text.lower() or "cancel" in resp.text.lower()

    @patch("hozo.core.job.run_restore_job")
    def test_post_restore_correct_name_redirects(
        self, mock_restore: MagicMock, client: TestClient
    ) -> None:
        from datetime import datetime, timezone

        from hozo.core.job import JobResult

        mock_restore.return_value = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
        )
        resp = client.post(
            "/jobs/weekly/restore",
            data={"confirm_name": "weekly"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/restore/log" in resp.headers["location"]

    def test_get_restore_log_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/restore/log")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_get_restore_log_lines_returns_html(self, client: TestClient) -> None:
        resp = client.get("/jobs/weekly/restore/log/lines")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_restore_unknown_job_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/ghost/restore",
            data={"confirm_name": "ghost"},
            follow_redirects=False,
        )
        assert resp.status_code == 404


class TestResultsEndpointWithData:
    def test_result_found_returns_json(self, client: TestClient) -> None:
        from datetime import datetime, timezone

        from hozo.core.job import JobResult

        client.app.state.last_results["weekly"] = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
        )
        resp = client.get("/results/weekly")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_name"] == "weekly"
        assert data["success"] is True


class TestShutdownExceptionCaught:
    """Test that shutdown route handles SSH exception gracefully."""

    def test_shutdown_ssh_exception_returns_ok(self, client: TestClient) -> None:
        """If the SSH command raises (machine already off), route still returns 200."""
        with patch("hozo.core.ssh.run_command", side_effect=Exception("Connection reset")):
            resp = client.post("/shutdown", json={"job_name": "weekly"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "shutdown_sent"


class TestSettingsConditionalFields:
    """Test settings POST with optional notification fields."""

    def test_settings_with_ntfy_url(self, client: TestClient) -> None:
        resp = client.post(
            "/settings",
            data={
                "ssh_timeout": "90",
                "ssh_user": "backup",
                "ntfy_url": "https://ntfy.sh/hozo-test",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert client.app.state.settings.get("ntfy_url") == "https://ntfy.sh/hozo-test"

    def test_settings_with_pushover_keys(self, client: TestClient) -> None:
        resp = client.post(
            "/settings",
            data={
                "ssh_timeout": "90",
                "ssh_user": "backup",
                "pushover_user_key": "ukeyxyz",
                "pushover_api_token": "tokenabc",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert client.app.state.settings.get("pushover_user_key") == "ukeyxyz"

    def test_settings_with_smtp_fields(self, client: TestClient) -> None:
        resp = client.post(
            "/settings",
            data={
                "ssh_timeout": "90",
                "ssh_user": "backup",
                "smtp_host": "mail.example.com",
                "smtp_port": "587",
                "smtp_user": "hozo@example.com",
                "smtp_to": "admin@example.com",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert client.app.state.settings.get("smtp_host") == "mail.example.com"


class TestJobFormOptionalFields:
    """Test job CRUD with optional fields (ssh_key, schedule, etc.)."""

    def test_create_job_with_schedule(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "scheduled_job",
                "source_dataset": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "11:22:33:44:55:66",
                "schedule": "daily 03:00",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        names = [j.name for j in client.app.state.jobs]
        assert "scheduled_job" in names

    def test_create_job_with_ssh_key(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "key_job",
                "source_dataset": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "11:22:33:44:55:AA",
                "ssh_key": "/root/.ssh/id_ed25519",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_create_job_with_backup_device(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "disk_job",
                "source_dataset": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "11:22:33:44:55:BB",
                "backup_device": "/dev/sdb",
                "disk_spinup_timeout": "120",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestAuthWithCredentials:
    """Auth routes when credentials exist in state."""

    @pytest.fixture
    def authed_client(self, tmp_path: Path) -> TestClient:
        """Client with a credential seed in auth state."""
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\x01\x02\x03",
                public_key=b"\x04\x05\x06",
                sign_count=0,
                device_name="Test Key",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            return TestClient(app)

    def test_login_with_creds_shows_login_page(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/auth/login")
        assert resp.status_code == 200
        assert "Register" in resp.text or "Login" in resp.text or "webauthn" in resp.text.lower()

    def test_devices_shows_registered_device(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/auth/devices")
        assert resp.status_code == 200
        assert "Test Key" in resp.text or "devices" in resp.text.lower()


# ── Middleware branch coverage ────────────────────────────────────────────────


class TestMiddlewareBranches:
    """Cover middleware no-creds and open-path branches."""

    def test_middleware_no_creds_passes_through(self, client: TestClient) -> None:
        # No credentials in state, so the middleware's first branch does call_next
        assert not client.app.state.auth.get("credentials")
        resp = client.get("/")
        assert resp.status_code == 200

    def test_middleware_open_path_passes_through(self, client: TestClient) -> None:
        # /auth/login is in _OPEN_PATHS, middleware lets it through without auth
        resp = client.get("/auth/login", follow_redirects=False)
        # With no credentials configured, /auth/login redirects to /auth/register
        assert resp.status_code in (200, 302)

    def test_middleware_api_returns_401_when_authed_app(self, tmp_path: Path) -> None:
        """When credentials exist and no valid cookie, /status → 401."""
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\x01\x02\x03",
                public_key=b"\x04\x05\x06",
                sign_count=0,
                device_name="Test Key",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/status", follow_redirects=False)
        assert resp.status_code == 401

    def test_middleware_html_redirects_to_login_when_authed_app(self, tmp_path: Path) -> None:
        """When credentials exist and no valid cookie, HTML routes → redirect."""
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\xAA\xBB\xCC",
                public_key=b"\x01\x02\x03",
                sign_count=0,
                device_name="Key2",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


# ── Bootstrap (no config file) coverage ──────────────────────────────────────


class TestBootstrapNoConfig:
    """Cover the else branch when config file does not exist."""

    def test_app_starts_without_config(self, tmp_path: Path) -> None:
        from hozo.api.routes import create_app

        missing_path = tmp_path / "nonexistent.yaml"
        app = create_app(config_path=str(missing_path))
        c = TestClient(app)
        resp = c.get("/")
        assert resp.status_code == 200
        # session_secret should have been seeded in memory
        assert app.state.auth.get("session_secret")


# ── Scheduler hot-reload with existing scheduler ──────────────────────────────


class TestSchedulerHotReload:
    """Cover _load_jobs_and_scheduler when a scheduler already exists."""

    def test_settings_post_restarts_existing_scheduler(self, tmp_path: Path) -> None:
        from hozo.api.routes import create_app

        config_path = _write_config(tmp_path)
        mock_sched = MagicMock()

        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            # Inject a fake existing scheduler so the stop() branch runs
            app.state.scheduler = mock_sched
            c = TestClient(app)
            resp = c.post(
                "/settings",
                data={"ssh_timeout": "60", "ssh_user": "root"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        mock_sched.stop.assert_called()

    def test_scheduler_stop_exception_swallowed(self, tmp_path: Path) -> None:
        from hozo.api.routes import create_app

        config_path = _write_config(tmp_path)
        mock_sched = MagicMock()
        mock_sched.stop.side_effect = RuntimeError("stop failed")

        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            app.state.scheduler = mock_sched
            c = TestClient(app)
            # Should not raise despite stop() throwing
            resp = c.post(
                "/settings",
                data={"ssh_timeout": "60", "ssh_user": "root"},
                follow_redirects=False,
            )
        assert resp.status_code == 303


# ── Job form with description and broadcast_ip ────────────────────────────────


class TestJobFormAllFields:
    """Cover _apply_job_form description and broadcast_ip branches."""

    def test_create_job_with_description(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "desc_job",
                "source_dataset": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "11:22:33:44:55:CD",
                "description": "My test job description",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        raw_jobs = client.app.state.raw_config.get("jobs", [])
        desc_entry = next((j for j in raw_jobs if j["name"] == "desc_job"), None)
        assert desc_entry is not None
        assert desc_entry.get("description") == "My test job description"

    def test_create_job_with_broadcast_ip(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/new",
            data={
                "name": "bcast_job",
                "source_dataset": "rpool/data",
                "target_host": "backup.local",
                "target_dataset": "backup/data",
                "mac_address": "11:22:33:44:55:DE",
                "broadcast_ip": "192.168.1.255",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        raw_jobs = client.app.state.raw_config.get("jobs", [])
        bcast_entry = next((j for j in raw_jobs if j["name"] == "bcast_job"), None)
        assert bcast_entry is not None
        assert bcast_entry.get("broadcast_ip") == "192.168.1.255"


# ── WebAuthn API routes ───────────────────────────────────────────────────────


class TestWebAuthnLoginBegin:
    """Cover POST /auth/login/begin."""

    @pytest.fixture
    def authed_client(self, tmp_path: Path) -> TestClient:
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\xAA\xBB",
                public_key=b"\x01\x02",
                sign_count=0,
                device_name="HW Key",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            return TestClient(app)

    @patch("hozo.api.routes.begin_authentication")
    def test_login_begin_returns_options(self, mock_begin: MagicMock, authed_client: TestClient) -> None:
        mock_begin.return_value = ('{"publicKey":"opts"}', b"\x01\x02\x03")
        resp = authed_client.post("/auth/login/begin")
        assert resp.status_code == 200
        assert resp.json() == {"publicKey": "opts"}
        assert len(authed_client.app.state.pending_challenges) == 1


class TestWebAuthnLoginComplete:
    """Cover POST /auth/login/complete."""

    @pytest.fixture
    def authed_client(self, tmp_path: Path) -> TestClient:
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\xAA\xBB",
                public_key=b"\x01\x02",
                sign_count=0,
                device_name="HW Key",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            return TestClient(app)

    def test_login_complete_no_pending_challenge_returns_400(self, authed_client: TestClient) -> None:
        # No pending challenges seeded
        assert not authed_client.app.state.pending_challenges
        resp = authed_client.post("/auth/login/complete", content=b'{"id":"abc"}')
        assert resp.status_code == 400
        assert "No pending challenge" in resp.json()["error"]

    @patch("hozo.api.routes.complete_authentication")
    def test_login_complete_exception_returns_400(self, mock_complete: MagicMock, authed_client: TestClient) -> None:
        from hozo.auth.webauthn_helpers import store_challenge
        challenge = b"\x01\x02\x03\x04"
        store_challenge(authed_client.app.state.pending_challenges, challenge)
        mock_complete.side_effect = Exception("Bad signature")
        resp = authed_client.post("/auth/login/complete", content=b'{"id":"x"}')
        assert resp.status_code == 400
        assert "Bad signature" in resp.json()["error"]

    @patch("hozo.api.routes.complete_authentication")
    def test_login_complete_success_sets_cookie(self, mock_complete: MagicMock, authed_client: TestClient) -> None:
        from hozo.auth.webauthn_helpers import StoredCredential, store_challenge

        challenge = b"\x01\x02\x03\x04"
        store_challenge(authed_client.app.state.pending_challenges, challenge)
        updated_cred = StoredCredential(
            credential_id=b"\xAA\xBB",
            public_key=b"\x01\x02",
            sign_count=1,
            device_name="HW Key",
        )
        mock_complete.return_value = updated_cred
        resp = authed_client.post("/auth/login/complete", content=b'{"id":"x"}')
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert "hozo_session" in resp.cookies or resp.headers.get("set-cookie")


class TestWebAuthnRegisterBegin:
    """Cover POST /auth/register/begin."""

    @patch("hozo.api.routes.begin_registration")
    def test_register_begin_returns_options(self, mock_begin: MagicMock, client: TestClient) -> None:
        mock_begin.return_value = ('{"rp":"localhost"}', b"\xDE\xAD")
        resp = client.post("/auth/register/begin")
        assert resp.status_code == 200
        assert resp.json() == {"rp": "localhost"}
        assert len(client.app.state.pending_challenges) == 1


class TestWebAuthnRegisterComplete:
    """Cover POST /auth/register/complete."""

    def test_register_complete_no_challenge_returns_400(self, client: TestClient) -> None:
        assert not client.app.state.pending_challenges
        resp = client.post("/auth/register/complete", content=b'{"id":"abc"}')
        assert resp.status_code == 400
        assert "No pending challenge" in resp.json()["error"]

    @patch("hozo.api.routes.complete_registration")
    def test_register_complete_exception_returns_400(self, mock_complete: MagicMock, client: TestClient) -> None:
        from hozo.auth.webauthn_helpers import store_challenge

        challenge = b"\x05\x06\x07\x08"
        store_challenge(client.app.state.pending_challenges, challenge)
        mock_complete.side_effect = Exception("Invalid CBOR")
        resp = client.post(
            "/auth/register/complete",
            content=b'{"id":"bad"}',
            headers={"x-device-name": "My Key"},
        )
        assert resp.status_code == 400
        assert "Invalid CBOR" in resp.json()["error"]

    @patch("hozo.api.routes.complete_registration")
    def test_register_complete_success(self, mock_complete: MagicMock, client: TestClient) -> None:
        from hozo.auth.webauthn_helpers import StoredCredential, store_challenge

        challenge = b"\x05\x06\x07\x08"
        store_challenge(client.app.state.pending_challenges, challenge)
        new_cred = StoredCredential(
            credential_id=b"\x10\x11\x12",
            public_key=b"\x20\x21\x22",
            sign_count=0,
            device_name="New Key",
        )
        mock_complete.return_value = new_cred
        resp = client.post(
            "/auth/register/complete",
            content=b'{"id":"ok"}',
            headers={"x-device-name": "New Key"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"
        assert resp.json()["device"] == "New Key"
        creds = client.app.state.auth.get("credentials", [])
        assert len(creds) == 1


class TestWebAuthnDeviceDelete:
    """Cover POST /auth/devices/{cred_id}/delete."""

    @pytest.fixture
    def authed_client(self, tmp_path: Path) -> TestClient:
        from hozo.api.routes import create_app
        from hozo.auth.session import make_session_cookie
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\xAA\xBB\xCC",
                public_key=b"\x01\x02\x03",
                sign_count=0,
                device_name="To Delete",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            secret = app.state.auth.get("session_secret", "test_secret")
            app.state.auth["session_secret"] = secret
            cookie_val = make_session_cookie(secret)
            c = TestClient(app)
            c.cookies.set("hozo_session", cookie_val)
            return c

    def test_delete_device_removes_credential(self, authed_client: TestClient) -> None:
        import base64

        cred_id = base64.urlsafe_b64encode(b"\xAA\xBB\xCC").decode().rstrip("=")
        assert len(authed_client.app.state.auth["credentials"]) == 1
        resp = authed_client.post(
            f"/auth/devices/{cred_id}/delete", follow_redirects=False
        )
        assert resp.status_code == 303
        assert authed_client.app.state.auth["credentials"] == []

    def test_delete_nonexistent_device_leaves_list_unchanged(self, authed_client: TestClient) -> None:
        import base64

        wrong_id = base64.urlsafe_b64encode(b"\xFF\xFF\xFF").decode().rstrip("=")
        resp = authed_client.post(
            f"/auth/devices/{wrong_id}/delete", follow_redirects=False
        )
        assert resp.status_code == 303
        # Original credential still present
        assert len(authed_client.app.state.auth["credentials"]) == 1


# ── Additional coverage for remaining lines ───────────────────────────────────


class TestPartialJobsRoute:
    """Cover GET /partials/jobs (line 244)."""

    def test_partials_jobs_returns_html(self, client: TestClient) -> None:
        resp = client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestDetectOriginWithHeader:
    """Cover _detect_origin when origin header is present (line 210)."""

    @patch("hozo.api.routes.complete_registration")
    def test_register_complete_uses_origin_header(
        self, mock_complete: MagicMock, client: TestClient
    ) -> None:
        from hozo.auth.webauthn_helpers import StoredCredential, store_challenge

        challenge = b"\x09\x0A\x0B\x0C"
        store_challenge(client.app.state.pending_challenges, challenge)
        new_cred = StoredCredential(
            credential_id=b"\x30\x31\x32",
            public_key=b"\x40\x41\x42",
            sign_count=0,
            device_name="Origin Key",
        )
        mock_complete.return_value = new_cred
        resp = client.post(
            "/auth/register/complete",
            content=b'{"id":"ok"}',
            headers={"x-device-name": "Origin Key", "origin": "https://myhost.example.com"},
        )
        assert resp.status_code == 200
        # Confirm origin was passed to complete_registration
        call_kwargs = mock_complete.call_args
        assert call_kwargs.args[3] == "https://myhost.example.com"


class TestLoginCompleteNonMatchingCred:
    """Cover login complete when loop has a non-matching credential (line 571)."""

    @pytest.fixture
    def multi_cred_client(self, tmp_path: Path) -> TestClient:
        from hozo.api.routes import create_app
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred1 = StoredCredential(
                credential_id=b"\xAA\xBB",
                public_key=b"\x01\x02",
                sign_count=0,
                device_name="Key1",
            )
            cred2 = StoredCredential(
                credential_id=b"\xCC\xDD",
                public_key=b"\x03\x04",
                sign_count=0,
                device_name="Key2",
            )
            app.state.auth["credentials"] = [cred1.to_dict(), cred2.to_dict()]
            return TestClient(app)

    @patch("hozo.api.routes.complete_authentication")
    def test_login_complete_updates_only_matching_cred(
        self, mock_complete: MagicMock, multi_cred_client: TestClient
    ) -> None:
        from hozo.auth.webauthn_helpers import StoredCredential, store_challenge

        challenge = b"\x01\x02\x03\x04"
        store_challenge(multi_cred_client.app.state.pending_challenges, challenge)
        # Return an updated version of cred1 (b"\xAA\xBB")
        updated = StoredCredential(
            credential_id=b"\xAA\xBB",
            public_key=b"\x01\x02",
            sign_count=1,
            device_name="Key1",
        )
        mock_complete.return_value = updated
        resp = multi_cred_client.post("/auth/login/complete", content=b'{"id":"x"}')
        assert resp.status_code == 200
        # cred1 updated, cred2 kept as-is (the else branch at line 571)
        saved = multi_cred_client.app.state.auth["credentials"]
        assert len(saved) == 2


class TestLoadJobsSchedulerEdgeCases:
    """Cover _load_jobs_and_scheduler when config file is deleted or empty."""

    def test_save_config_when_load_config_empty(self, tmp_path: Path) -> None:
        """Covers line 122: load_config returns empty dict → early return in _load_jobs_and_scheduler."""
        from hozo.api.routes import create_app

        config_path = _write_config(tmp_path)

        call_count = 0

        def load_config_side_effect(path):
            nonlocal call_count
            call_count += 1
            # First call is during create_app initialization — return real value
            # Subsequent calls (during _load_jobs_and_scheduler via _save_config)
            # return {} to trigger the "if not raw: return" branch
            if call_count <= 1:
                import yaml
                with open(path) as f:
                    return yaml.safe_load(f) or {}
            return {}

        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
            patch("hozo.config.loader.load_config", side_effect=load_config_side_effect),
        ):
            app = create_app(config_path=str(config_path))
            c = TestClient(app)
            resp = c.post(
                "/settings",
                data={"ssh_timeout": "30", "ssh_user": "root"},
                follow_redirects=False,
            )
        assert resp.status_code == 303

    def test_save_config_when_config_file_missing_after_write(self, tmp_path: Path) -> None:
        """Covers line 119: config file not found when _load_jobs_and_scheduler runs."""
        from hozo.api.routes import create_app

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            c = TestClient(app)
            # Patch Path.exists to return False so line 119 (early return) fires
            with patch("pathlib.Path.exists", return_value=False):
                resp = c.post(
                    "/settings",
                    data={"ssh_timeout": "30", "ssh_user": "root"},
                    follow_redirects=False,
                )
        assert resp.status_code == 303

    def test_get_devices_with_credentials_shows_device_info(self, tmp_path: Path) -> None:
        """Covers lines 640-641: get_devices loop body with an authenticated client."""
        from hozo.api.routes import create_app
        from hozo.auth.session import make_session_cookie
        from hozo.auth.webauthn_helpers import StoredCredential

        config_path = _write_config(tmp_path)
        with (
            patch("hozo.scheduler.runner.HozoScheduler.start"),
            patch("hozo.scheduler.runner.HozoScheduler.stop"),
            patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=1),
        ):
            app = create_app(config_path=str(config_path))
            cred = StoredCredential(
                credential_id=b"\xAA\xBB\xCC",
                public_key=b"\x01\x02\x03",
                sign_count=7,
                device_name="My Yubikey",
            )
            app.state.auth["credentials"] = [cred.to_dict()]
            secret = app.state.auth.get("session_secret", "s3cret")
            app.state.auth["session_secret"] = secret
            cookie_val = make_session_cookie(secret)
            c = TestClient(app)
            c.cookies.set("hozo_session", cookie_val)

        resp = c.get("/auth/devices")
        assert resp.status_code == 200
        assert "My Yubikey" in resp.text or "devices" in resp.text.lower()


class TestOnResultCallback:
    """Cover _on_result callback (line 140) via scheduler result injection."""

    def test_on_result_updates_last_results(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone

        from hozo.api.routes import create_app
        from hozo.core.job import JobResult

        config_path = _write_config(tmp_path)
        captured_callback = None

        class CapturingScheduler:
            """Captures on_result callback from HozoScheduler init."""

            def __init__(self, on_result=None, **kwargs):
                nonlocal captured_callback
                captured_callback = on_result

            def load_jobs_from_config(self, *args, **kwargs):
                return 1

            def start(self):
                pass

            def stop(self):
                pass

        with patch("hozo.scheduler.runner.HozoScheduler", CapturingScheduler):
            app = create_app(config_path=str(config_path))

        assert captured_callback is not None

        now = datetime.now(timezone.utc)
        result = JobResult(
            job_name="weekly",
            success=True,
            started_at=now,
            finished_at=now,
            snapshots_after=["pool@snap1"],
            log_lines=["done"],
            attempts=1,
        )
        captured_callback(result)
        assert app.state.last_results["weekly"] is result
