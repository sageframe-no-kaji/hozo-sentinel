"""FastAPI routes for the Hōzō web UI and API."""

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from hozo.api.models import (
    BackupRequest,
    JobResultResponse,
    JobStatusResponse,
    ShutdownRequest,
    StatusResponse,
    WakeRequest,
)
from hozo.core.job import JobResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# In-memory store for last job results (keyed by job name)
_last_results: dict[str, JobResult] = {}


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


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        config_path: Path to hozo config.yaml. If None, uses the default location.

    Returns:
        FastAPI application instance
    """
    from hozo.config.loader import jobs_from_config, load_config, validate_config
    from hozo.scheduler.runner import HozoScheduler

    _config_path = Path(config_path) if config_path else Path.home() / ".config/hozo/config.yaml"

    # Mutable state container shared across all routes
    state: dict[str, Any] = {"scheduler": None, "jobs": []}

    def _on_result(result: JobResult) -> None:
        _last_results[result.job_name] = result

    # Load config and start scheduler eagerly (synchronous, at app creation time)
    if _config_path.exists():
        raw = load_config(_config_path)
        if raw:
            errors = validate_config(raw)
            if errors:
                logger.warning("Config validation errors: %s", errors)
            else:
                state["jobs"] = jobs_from_config(raw)
                sched = HozoScheduler(on_result=_on_result)
                sched.load_jobs_from_config(_config_path)
                sched.start()
                state["scheduler"] = sched
                logger.info("Scheduler started with %d job(s)", len(state["jobs"]))
    else:
        logger.warning("Config not found at %s — running without jobs", _config_path)

    app = FastAPI(
        title="Hōzō",
        version="0.1.0",
        description="Wake-on-demand ZFS backup orchestrator",
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # ── HTML Dashboard ────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        jobs = state["jobs"]
        job_statuses = []
        for j in jobs:
            last = _last_results.get(j.name)
            job_statuses.append(
                {
                    "name": j.name,
                    "description": j.description,
                    "source": j.source_dataset,
                    "target_host": j.target_host,
                    "target_dataset": j.target_dataset,
                    "shutdown_after": j.shutdown_after,
                    "last_result": last,
                }
            )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": job_statuses,
                "scheduler_running": state["scheduler"] is not None,
            },
        )

    # ── Partial: job cards (used by HTMX polling) ─────────────────────────────

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def partial_jobs(request: Request) -> HTMLResponse:
        jobs = state["jobs"]
        job_statuses = []
        for j in jobs:
            last = _last_results.get(j.name)
            job_statuses.append(
                {
                    "name": j.name,
                    "description": j.description,
                    "source": j.source_dataset,
                    "target_host": j.target_host,
                    "target_dataset": j.target_dataset,
                    "shutdown_after": j.shutdown_after,
                    "last_result": last,
                }
            )
        return templates.TemplateResponse(
            request,
            "partials/job_cards.html",
            {"jobs": job_statuses},
        )

    # ── JSON API ──────────────────────────────────────────────────────────────

    @app.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        jobs = state["jobs"]
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
                for j in jobs
            ],
            scheduler_running=state["scheduler"] is not None,
        )

    @app.post("/wake")
    async def post_wake(req: WakeRequest, background_tasks: BackgroundTasks) -> JSONResponse:
        jobs = state["jobs"]
        job = next((j for j in jobs if j.name == req.job_name), None)
        if not job:
            return JSONResponse({"error": f"Job '{req.job_name}' not found"}, status_code=404)

        from hozo.core.wol import wake as do_wake

        background_tasks.add_task(do_wake, job.mac_address, job.wol_broadcast)
        return JSONResponse({"status": "wol_sent", "job": req.job_name, "mac": job.mac_address})

    @app.post("/run_backup")
    async def post_run_backup(
        req: BackupRequest, background_tasks: BackgroundTasks
    ) -> JSONResponse:
        jobs = state["jobs"]
        job = next((j for j in jobs if j.name == req.job_name), None)
        if not job:
            return JSONResponse({"error": f"Job '{req.job_name}' not found"}, status_code=404)

        from hozo.core.job import run_job

        def _run() -> None:
            result = run_job(job)
            _last_results[result.job_name] = result

        background_tasks.add_task(_run)
        return JSONResponse({"status": "started", "job": req.job_name})

    @app.post("/shutdown")
    async def post_shutdown(
        req: ShutdownRequest, background_tasks: BackgroundTasks
    ) -> JSONResponse:
        jobs = state["jobs"]
        job = next((j for j in jobs if j.name == req.job_name), None)
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
        result = _last_results.get(job_name)
        if not result:
            return JSONResponse({"error": f"No result found for job '{job_name}'"}, status_code=404)
        resp = _result_to_response(result)
        return JSONResponse(resp.model_dump())

    return app
