"""FastAPI routes for the Hōzō web UI and API."""

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from hozo.api.models import (
    BackupRequest,
    JobResultResponse,
    JobStatusResponse,
    ShutdownRequest,
    StatusResponse,
    WakeRequest,
)
from hozo.auth.session import (
    COOKIE_NAME,
    generate_secret,
    make_session_cookie,
    verify_session_cookie,
)
from hozo.auth.webauthn_helpers import (
    StoredCredential,
    begin_authentication,
    begin_registration,
    complete_authentication,
    complete_registration,
    pop_challenge,
    store_challenge,
)
from hozo.core.job import JobResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_API_PREFIXES = ("/status", "/results/", "/wake", "/run_backup", "/shutdown", "/disk/")

_OPEN_PATHS = {
    "/auth/login",
    "/auth/login/begin",
    "/auth/login/complete",
    "/auth/logout",
    "/auth/register/begin",
    "/auth/register/complete",
    "/favicon.ico",
}


class HozoAuthMiddleware(BaseHTTPMiddleware):
    """Block unauthenticated requests once any WebAuthn credential is registered."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        state = request.app.state
        creds: list[dict[str, Any]] = state.auth.get("credentials", [])

        if not creds:
            return await call_next(request)

        if path in _OPEN_PATHS:
            return await call_next(request)

        cookie = request.cookies.get(COOKIE_NAME, "")
        secret: str = state.auth.get("session_secret", "")
        if cookie and verify_session_cookie(cookie, secret):
            return await call_next(request)

        if any(path.startswith(p) for p in _API_PREFIXES):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return RedirectResponse(f"/auth/login?next={request.url.path}", status_code=302)


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        config_path: Path to hozo config.yaml. If None, uses the default location.

    Returns:
        FastAPI application instance
    """
    from hozo.config.loader import jobs_from_config, load_config, validate_config
    from hozo.config.writer import write_config
    from hozo.scheduler.runner import HozoScheduler

    _config_path = Path(config_path) if config_path else Path.home() / ".config/hozo/config.yaml"

    app = FastAPI(
        title="Hōzō",
        version="0.1.0",
        description="Wake-on-demand ZFS backup orchestrator",
    )

    # ── App state ─────────────────────────────────────────────────────────────
    app.state.config_path = _config_path
    app.state.raw_config = {}
    app.state.settings = {}
    app.state.auth = {}
    app.state.jobs = []
    app.state.last_results = {}
    app.state.scheduler = None
    app.state.pending_challenges = {}

    def _load_jobs_and_scheduler() -> None:
        """Hot-reload jobs + restart scheduler from current config file."""
        if not _config_path.exists():
            return
        raw = load_config(_config_path)
        if not raw:
            return
        # Always sync state from disk regardless of validation outcome.
        app.state.raw_config = raw
        app.state.settings = raw.get("settings", {})
        app.state.auth = raw.get("auth", {})
        app.state.jobs = jobs_from_config(raw)
        errors = validate_config(raw)
        if errors:
            logger.warning("Config validation errors: %s", errors)
            return  # Don't (re)start scheduler with invalid config

        if app.state.scheduler is not None:
            try:
                app.state.scheduler.stop()
            except Exception:
                pass

        def _on_result(result: JobResult) -> None:
            app.state.last_results[result.job_name] = result

        sched = HozoScheduler(on_result=_on_result)
        sched.load_jobs_from_config(_config_path)
        sched.start()
        app.state.scheduler = sched
        logger.info("Scheduler started with %d job(s)", len(app.state.jobs))

    # Seed auth.session_secret if the config exists but has no secret yet
    if _config_path.exists():
        raw_seed = load_config(_config_path) or {}
        auth_seed: dict[str, Any] = raw_seed.get("auth", {})
        if not auth_seed.get("session_secret"):
            auth_seed["session_secret"] = generate_secret()
            raw_seed["auth"] = auth_seed
            write_config(_config_path, raw_seed)
        _load_jobs_and_scheduler()
    else:
        # Bootstrap: no config file yet — seed a session secret in memory
        # so the first credential save has a stable secret to write.
        app.state.auth["session_secret"] = generate_secret()
        logger.warning("Config not found at %s — running without jobs", _config_path)

    app.add_middleware(HozoAuthMiddleware)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _result_to_response(r: JobResult) -> JobResultResponse:
        return JobResultResponse(
            job_name=r.job_name,
            success=r.success,
            started_at=r.started_at,
            finished_at=r.finished_at,
            error=r.error,
            duration_seconds=r.duration_seconds,
            snapshot_count=len(r.snapshots_after),
            attempts=r.attempts,
        )

    def _job_statuses() -> list[dict[str, Any]]:
        result = []
        for j in app.state.jobs:
            last = app.state.last_results.get(j.name)
            result.append(
                {
                    "name": j.name,
                    "description": j.description,
                    "source": j.source_dataset,
                    "target_host": j.target_host,
                    "target_dataset": j.target_dataset,
                    "shutdown_after": j.shutdown_after,
                    "schedule": getattr(j, "schedule", ""),
                    "backup_device": getattr(j, "backup_device", ""),
                    "last_result": last,
                }
            )
        return result

    def _is_authed(request: Request) -> bool:
        secret = app.state.auth.get("session_secret", "")
        cookie = request.cookies.get(COOKIE_NAME, "")
        return bool(cookie and secret and verify_session_cookie(cookie, secret))

    def _is_localhost(rp_id: str) -> bool:
        return rp_id in ("localhost", "127.0.0.1", "::1")

    def _detect_origin(request: Request, rp_id: str) -> str:
        origin = request.headers.get("origin", "")
        if origin:
            return origin
        scheme = "http" if _is_localhost(rp_id) else "https"
        return f"{scheme}://{rp_id}"

    def _save_config() -> None:
        # Sync mutable sub-dicts back into raw_config before writing.
        app.state.raw_config["settings"] = app.state.settings
        app.state.raw_config["auth"] = app.state.auth
        write_config(app.state.config_path, app.state.raw_config)
        _load_jobs_and_scheduler()

    def _tpl(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
        creds = app.state.auth.get("credentials", [])
        ctx.setdefault("is_authenticated", _is_authed(request))
        ctx.setdefault("credentials_exist", bool(creds))
        return templates.TemplateResponse(request, name, ctx)

    # ── HTML Dashboard ────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return _tpl(
            request,
            "dashboard.html",
            {
                "jobs": _job_statuses(),
                "scheduler_running": app.state.scheduler is not None,
            },
        )

    # ── Partial: job cards (used by HTMX polling) ─────────────────────────────

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def partial_jobs(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "partials/job_cards.html",
            {"jobs": _job_statuses()},
        )

    # ── Settings ──────────────────────────────────────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    async def get_settings(request: Request) -> HTMLResponse:
        s = app.state.settings
        auth = app.state.auth
        return _tpl(
            request,
            "settings.html",
            {
                "ssh_timeout": s.get("ssh_timeout", 30),
                "ssh_user": s.get("ssh_user", "root"),
                "ntfy_url": s.get("ntfy_url", ""),
                "pushover_user_key": s.get("pushover_user_key", ""),
                "pushover_api_token": s.get("pushover_api_token", ""),
                "smtp_host": s.get("smtp_host", ""),
                "smtp_port": s.get("smtp_port", 587),
                "smtp_user": s.get("smtp_user", ""),
                "smtp_to": s.get("smtp_to", ""),
                "rp_id": auth.get("rp_id", "localhost"),
                "saved": False,
            },
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def post_settings(request: Request) -> Response:
        form = await request.form()
        s = app.state.settings
        s["ssh_timeout"] = int(str(form.get("ssh_timeout", s.get("ssh_timeout", 30))))
        s["ssh_user"] = str(form.get("ssh_user", s.get("ssh_user", "root")))
        if form.get("ntfy_url"):
            s["ntfy_url"] = str(form["ntfy_url"])
        if form.get("pushover_user_key"):
            s["pushover_user_key"] = str(form["pushover_user_key"])
        if form.get("pushover_api_token"):
            s["pushover_api_token"] = str(form["pushover_api_token"])
        if form.get("smtp_host"):
            s["smtp_host"] = str(form["smtp_host"])
        if form.get("smtp_port"):
            s["smtp_port"] = int(str(form["smtp_port"]))
        if form.get("smtp_user"):
            s["smtp_user"] = str(form["smtp_user"])
        if form.get("smtp_to"):
            s["smtp_to"] = str(form["smtp_to"])
        rp_id = str(form.get("rp_id", app.state.auth.get("rp_id", "localhost")))
        app.state.auth["rp_id"] = rp_id
        app.state.raw_config["settings"] = s
        _save_config()
        return RedirectResponse("/settings?saved=1", status_code=303)

    # ── Job CRUD ──────────────────────────────────────────────────────────────

    def _apply_job_form(form: Any, name: str, *, is_new: bool) -> None:
        """Parse a submitted job form and upsert the job in raw_config."""
        raw_jobs: list[dict[str, Any]] = app.state.raw_config.get("jobs", [])
        entry: dict[str, Any] = {
            "name": name,
            "source": str(form.get("source_dataset", "")),
            "target_host": str(form.get("target_host", "")),
            "target_dataset": str(form.get("target_dataset", "")),
            "mac_address": str(form.get("mac_address", "")),
            "ssh_user": str(form.get("ssh_user", "root")),
            "ssh_port": int(form.get("ssh_port", 22)),
        }
        if form.get("description"):
            entry["description"] = str(form["description"])
        if form.get("schedule"):
            entry["schedule"] = str(form["schedule"])
        if form.get("ssh_key"):
            entry["ssh_key"] = str(form["ssh_key"])
        if form.get("ssh_timeout"):
            entry["ssh_timeout"] = int(form["ssh_timeout"])
        if form.get("broadcast_ip"):
            entry["broadcast_ip"] = str(form["broadcast_ip"])
        if form.get("backup_device"):
            entry["backup_device"] = str(form["backup_device"])
        if form.get("disk_spinup_timeout"):
            entry["disk_spinup_timeout"] = int(form["disk_spinup_timeout"])
        entry["shutdown_after"] = "shutdown_after" in form
        if is_new:
            raw_jobs.append(entry)
        else:
            raw_jobs = [e if e["name"] != name else entry for e in raw_jobs]
        app.state.raw_config["jobs"] = raw_jobs
        _save_config()

    @app.get("/jobs/new", response_class=HTMLResponse)
    async def get_job_new(request: Request) -> HTMLResponse:
        return _tpl(request, "job_form.html", {"job": None, "is_new": True, "title": "New Job"})

    @app.post("/jobs/new")
    async def post_job_new(request: Request) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        existing_names = [j.get("name") for j in app.state.raw_config.get("jobs", [])]
        if not name or name in existing_names:
            return RedirectResponse("/jobs/new?error=name", status_code=303)
        _apply_job_form(form, name, is_new=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/jobs/{job_name}/edit", response_class=HTMLResponse)
    async def get_job_edit(request: Request, job_name: str) -> Response:
        job = next((j for j in app.state.jobs if j.name == job_name), None)
        if job is None:
            return RedirectResponse("/", status_code=303)
        title = f"Edit Job — {job_name}"
        return _tpl(request, "job_form.html", {"job": job, "is_new": False, "title": title})

    @app.post("/jobs/{job_name}/edit")
    async def post_job_edit(request: Request, job_name: str) -> RedirectResponse:
        form = await request.form()
        _apply_job_form(form, job_name, is_new=False)
        return RedirectResponse("/", status_code=303)

    @app.post("/jobs/{job_name}/delete")
    async def post_job_delete(job_name: str) -> RedirectResponse:
        raw_jobs = app.state.raw_config.get("jobs", [])
        app.state.raw_config["jobs"] = [j for j in raw_jobs if j.get("name") != job_name]
        _save_config()
        return RedirectResponse("/", status_code=303)

    # ── JSON API ──────────────────────────────────────────────────────────────

    @app.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        return StatusResponse(
            jobs=[
                JobStatusResponse(
                    name=j.name,
                    source_dataset=j.source_dataset,
                    target_host=j.target_host,
                    target_dataset=j.target_dataset,
                    shutdown_after=j.shutdown_after,
                    description=j.description,
                )
                for j in app.state.jobs
            ],
            scheduler_running=app.state.scheduler is not None,
        )

    @app.post("/wake")
    async def post_wake(req: WakeRequest, background_tasks: BackgroundTasks) -> JSONResponse:
        job = next((j for j in app.state.jobs if j.name == req.job_name), None)
        if not job:
            return JSONResponse({"error": f"Job '{req.job_name}' not found"}, status_code=404)

        from hozo.core.wol import wake as do_wake

        background_tasks.add_task(do_wake, job.mac_address, job.wol_broadcast)
        return JSONResponse({"status": "wol_sent", "job": req.job_name, "mac": job.mac_address})

    @app.post("/run_backup")
    async def post_run_backup(
        req: BackupRequest, background_tasks: BackgroundTasks
    ) -> JSONResponse:
        job = next((j for j in app.state.jobs if j.name == req.job_name), None)
        if not job:
            return JSONResponse({"error": f"Job '{req.job_name}' not found"}, status_code=404)

        from hozo.core.job import run_job

        def _run() -> None:
            result = run_job(job)
            app.state.last_results[result.job_name] = result

        background_tasks.add_task(_run)
        return JSONResponse({"status": "started", "job": req.job_name})

    @app.post("/shutdown")
    async def post_shutdown(
        req: ShutdownRequest, background_tasks: BackgroundTasks
    ) -> JSONResponse:
        job = next((j for j in app.state.jobs if j.name == req.job_name), None)
        if not job:
            return JSONResponse({"error": f"Job '{req.job_name}' not found"}, status_code=404)

        from hozo.core.ssh import run_command

        def _shutdown() -> None:
            try:
                run_command(
                    job.target_host,
                    "shutdown -h now",
                    user=job.ssh_user,
                    port=job.ssh_port,
                    key_path=job.ssh_key,
                )
            except Exception as exc:
                logger.debug("Shutdown connection dropped (expected): %s", exc)

        background_tasks.add_task(_shutdown)
        return JSONResponse({"status": "shutdown_sent", "job": req.job_name})

    @app.get("/results/{job_name}", response_model=None)
    async def get_result(job_name: str) -> JSONResponse:
        result = app.state.last_results.get(job_name)
        if not result:
            return JSONResponse({"error": f"No result found for job '{job_name}'"}, status_code=404)
        return JSONResponse(_result_to_response(result).model_dump())

    # ── Job log viewer ────────────────────────────────────────────────────────

    @app.get("/jobs/{job_name}/log", response_class=HTMLResponse)
    async def get_job_log(request: Request, job_name: str) -> HTMLResponse:
        result = app.state.last_results.get(job_name)
        log_lines = list(result.log_lines) if result else []
        return _tpl(
            request,
            "job_log.html",
            {"job_name": job_name, "result": result, "log_lines": log_lines},
        )

    @app.get("/jobs/{job_name}/log/lines", response_class=HTMLResponse)
    async def get_job_log_lines(request: Request, job_name: str) -> HTMLResponse:
        result = app.state.last_results.get(job_name)
        log_lines = list(result.log_lines) if result else []
        return templates.TemplateResponse(
            request, "partials/log_lines.html", {"log_lines": log_lines}
        )

    # ── Break-glass restore ───────────────────────────────────────────────────

    @app.get("/jobs/{job_name}/restore", response_class=HTMLResponse)
    async def get_restore_confirm(request: Request, job_name: str) -> HTMLResponse:
        job = next((j for j in app.state.jobs if j.name == job_name), None)
        if not job:
            return HTMLResponse(f"Job '{job_name}' not found", status_code=404)
        return _tpl(request, "restore_confirm.html", {"job": job})

    @app.post("/jobs/{job_name}/restore", response_class=HTMLResponse)
    async def post_restore(
        request: Request,
        job_name: str,
        background_tasks: BackgroundTasks,
        confirm_name: str = Form(""),
    ) -> Response:
        job = next((j for j in app.state.jobs if j.name == job_name), None)
        if not job:
            return HTMLResponse(f"Job '{job_name}' not found", status_code=404)
        if confirm_name.strip() != job_name:
            return _tpl(
                request,
                "restore_confirm.html",
                {"job": job, "error": "Job name did not match. Restore cancelled."},
            )
        from hozo.core.job import run_restore_job

        def _run() -> None:
            result = run_restore_job(job)
            app.state.last_restore_results[result.job_name] = result

        background_tasks.add_task(_run)
        return RedirectResponse(f"/jobs/{job_name}/restore/log", status_code=303)

    @app.get("/jobs/{job_name}/restore/log", response_class=HTMLResponse)
    async def get_restore_log(
        request: Request, job_name: str
    ) -> HTMLResponse:
        result = app.state.last_restore_results.get(job_name)
        log_lines = list(result.log_lines) if result else []
        return _tpl(
            request,
            "restore_log.html",
            {"job_name": job_name, "result": result, "log_lines": log_lines},
        )

    @app.get("/jobs/{job_name}/restore/log/lines", response_class=HTMLResponse)
    async def get_restore_log_lines(
        request: Request, job_name: str
    ) -> HTMLResponse:
        result = app.state.last_restore_results.get(job_name)
        log_lines = list(result.log_lines) if result else []
        return templates.TemplateResponse(
            request, "partials/log_lines.html", {"log_lines": log_lines}
        )

    # ── Auth: login ───────────────────────────────────────────────────────────

    @app.get("/auth/login", response_class=HTMLResponse)
    async def get_login(request: Request) -> Response:
        creds = app.state.auth.get("credentials", [])
        if not creds:
            return RedirectResponse("/auth/register", status_code=302)
        next_url = request.query_params.get("next", "/")
        return _tpl(request, "auth/login.html", {"next": next_url, "has_credentials": True})

    @app.post("/auth/login/begin")
    async def post_login_begin(request: Request) -> JSONResponse:
        creds = app.state.auth.get("credentials", [])
        stored = [StoredCredential.from_dict(c) for c in creds]
        rp_id = app.state.auth.get("rp_id", "localhost")
        options_json, challenge = begin_authentication(rp_id, stored)
        store_challenge(app.state.pending_challenges, challenge)
        return JSONResponse(json.loads(options_json))

    @app.post("/auth/login/complete")
    async def post_login_complete(request: Request) -> JSONResponse:
        body = await request.body()
        creds = app.state.auth.get("credentials", [])
        stored = [StoredCredential.from_dict(c) for c in creds]
        rp_id = app.state.auth.get("rp_id", "localhost")
        origin = _detect_origin(request, rp_id)
        import base64

        # Find matching challenge — use the only pending one
        if not app.state.pending_challenges:
            return JSONResponse({"error": "No pending challenge"}, status_code=400)
        challenge = list(app.state.pending_challenges.keys())[0]
        challenge_bytes = base64.urlsafe_b64decode(challenge + "==")
        try:
            pop_challenge(app.state.pending_challenges, challenge_bytes)
            updated = complete_authentication(body.decode(), challenge_bytes, rp_id, origin, stored)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        # Persist updated sign count
        new_creds = []
        for c in creds:
            sc = StoredCredential.from_dict(c)
            if sc.credential_id == updated.credential_id:
                new_creds.append(updated.to_dict())
            else:
                new_creds.append(c)
        app.state.auth["credentials"] = new_creds
        _save_config()
        secret = app.state.auth.get("session_secret", generate_secret())
        cookie_val = make_session_cookie(secret)
        resp = JSONResponse({"status": "ok"})
        resp.set_cookie(
            COOKIE_NAME,
            cookie_val,
            httponly=True,
            samesite="lax",
            secure=not _is_localhost(rp_id),
            max_age=86400,
        )
        return resp

    @app.post("/auth/logout")
    async def post_logout() -> RedirectResponse:
        resp = RedirectResponse("/auth/login", status_code=302)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ── Auth: register ────────────────────────────────────────────────────────

    @app.get("/auth/register", response_class=HTMLResponse)
    async def get_register(request: Request) -> HTMLResponse:
        rp_id = app.state.auth.get("rp_id", "localhost")
        is_bootstrap = not bool(app.state.auth.get("credentials", []))
        return _tpl(request, "auth/register.html", {"rp_id": rp_id, "is_bootstrap": is_bootstrap})

    @app.post("/auth/register/begin")
    async def post_register_begin(request: Request) -> JSONResponse:
        rp_id = app.state.auth.get("rp_id", "localhost")
        options_json, challenge = begin_registration(rp_id, "Hōzō")
        store_challenge(app.state.pending_challenges, challenge)
        return JSONResponse(json.loads(options_json))

    @app.post("/auth/register/complete")
    async def post_register_complete(request: Request) -> JSONResponse:
        body = await request.body()
        rp_id = app.state.auth.get("rp_id", "localhost")
        origin = _detect_origin(request, rp_id)
        device_name = request.headers.get("x-device-name", "My Device")
        if not app.state.pending_challenges:
            return JSONResponse({"error": "No pending challenge"}, status_code=400)
        challenge_key = list(app.state.pending_challenges.keys())[0]
        import base64

        challenge_bytes = base64.urlsafe_b64decode(challenge_key + "==")
        try:
            pop_challenge(app.state.pending_challenges, challenge_bytes)
            cred = complete_registration(body.decode(), challenge_bytes, rp_id, origin, device_name)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        existing = app.state.auth.get("credentials", [])
        existing.append(cred.to_dict())
        app.state.auth["credentials"] = existing
        _save_config()
        return JSONResponse({"status": "registered", "device": device_name})

    # ── Auth: devices ─────────────────────────────────────────────────────────

    @app.get("/auth/devices", response_class=HTMLResponse)
    async def get_devices(request: Request) -> HTMLResponse:
        import base64

        creds = app.state.auth.get("credentials", [])
        devices = []
        for c in creds:
            sc = StoredCredential.from_dict(c)
            devices.append(
                {
                    "id": base64.urlsafe_b64encode(sc.credential_id).decode().rstrip("="),
                    "name": sc.device_name,
                    "added_at": sc.added_at.strftime("%Y-%m-%d %H:%M"),
                    "sign_count": sc.sign_count,
                }
            )
        return _tpl(request, "auth/devices.html", {"devices": devices})

    @app.post("/auth/devices/{cred_id}/delete")
    async def post_device_delete(cred_id: str, request: Request) -> RedirectResponse:
        import base64

        target = base64.urlsafe_b64decode(cred_id + "==")
        existing = app.state.auth.get("credentials", [])
        app.state.auth["credentials"] = [
            c for c in existing if StoredCredential.from_dict(c).credential_id != target
        ]
        _save_config()
        return RedirectResponse("/auth/devices", status_code=303)

    return app
