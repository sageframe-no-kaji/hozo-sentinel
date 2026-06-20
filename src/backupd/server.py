"""
backupd — lightweight HTTP agent for the Hōzō remote backup box.

Exposes:
    GET  /ping      → liveness probe (unauthenticated)
    GET  /status    → ZFS pool health + uptime (authenticated)
    POST /shutdown  → safe shutdown (export pools, then power off; authenticated)
    GET  /disk/{d}  → drive summary (authenticated)
    POST /disk/{d}/spinup → wake a standby drive (authenticated)

Every endpoint except ``/ping`` requires a bearer token supplied in the
``BACKUPD_TOKEN`` environment variable.  Without that variable set, the
authenticated endpoints fail closed (HTTP 503) — the agent can shut down the
machine and export pools, so an unauthenticated bind is never acceptable.

Deploy on the remote backup machine as a systemd service::

    [Unit]
    Description=Hōzō backup agent
    After=network.target

    [Service]
    Environment=BACKUPD_TOKEN=<a long random secret>
    # Bind the Tailscale/VPN interface, not every interface, where possible:
    Environment=BACKUPD_HOST=0.0.0.0
    ExecStart=/usr/local/bin/backupd
    Restart=always

    [Install]
    WantedBy=multi-user.target
"""

import logging
import os
import re
import secrets
import threading
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from backupd.disk import drive_summary, wait_for_drive_active
from backupd.system import get_uptime, safe_shutdown
from backupd.zfs import get_pool_status, list_pools

logger = logging.getLogger(__name__)

app = FastAPI(title="backupd", version="0.1.0", description="Hōzō remote backup agent")

# Block-device names are short alphanumerics (sda, sdb1, nvme0n1).  Anything
# else (slashes, dots, shell metacharacters) is rejected before it reaches the
# /dev path that hdparm/dd operate on.
_DEVICE_RE = re.compile(r"^[a-zA-Z0-9]+$")


def _expected_token() -> str:
    """Return the configured bearer token, or '' if BACKUPD_TOKEN is unset."""
    return os.environ.get("BACKUPD_TOKEN", "")


async def require_token(authorization: str = Header(default="")) -> None:
    """
    FastAPI dependency enforcing ``Authorization: Bearer <BACKUPD_TOKEN>``.

    Fails closed: if no token is configured the endpoint returns 503 rather
    than serving a privileged action without authentication.
    """
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="backupd token not configured (set BACKUPD_TOKEN)",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/ping")
async def ping() -> dict[str, Any]:
    """Liveness probe — returns immediately, no auth required."""
    return {"status": "ok", "uptime": get_uptime()}


@app.get("/status")
async def status(_: None = Depends(require_token)) -> dict[str, Any]:
    """Return ZFS pool health, pool list, and system uptime."""
    pools = list_pools()
    pool_states = get_pool_status()
    return {
        "uptime_seconds": get_uptime(),
        "pools": pools,
        "pool_states": pool_states,
    }


@app.post("/shutdown")
async def shutdown_endpoint(request: Request, _: None = Depends(require_token)) -> JSONResponse:
    """
    Schedule a safe shutdown.  Exports ZFS pools, then powers off.

    Returns 202 Accepted: the HTTP response is sent before the machine goes
    down, and ``safe_shutdown`` runs in a background thread.  The 202 is an
    honest "scheduled" — failures (e.g. the ``shutdown`` binary is missing)
    are logged at ERROR level in backupd's log, since the client connection
    has already been closed by the time they happen.
    """
    logger.info("Shutdown request received from %s", request.client)

    def _run_shutdown() -> None:
        if not safe_shutdown(export_pools=True, delay_seconds=2):
            logger.error("safe_shutdown returned False — machine is still up")

    t = threading.Thread(target=_run_shutdown)
    t.daemon = True
    t.start()
    return JSONResponse(
        {
            "status": "scheduled",
            "detail": "shutdown scheduled; check backupd logs for failures",
        },
        status_code=202,
    )


@app.get("/disk/{device}")
async def disk_status(device: str, _: None = Depends(require_token)) -> dict[str, Any]:
    """
    Return full drive summary for a device (e.g., ``/dev/sda`` → device=``sda``).

    Response includes ``state``, ``active`` (bool), and ``io_completions``.
    """
    if not _DEVICE_RE.match(device):
        raise HTTPException(status_code=400, detail="Invalid device name")
    dev_path = f"/dev/{device}"
    return drive_summary(dev_path)


@app.post("/disk/{device}/spinup")
async def disk_spinup(device: str, _: None = Depends(require_token)) -> JSONResponse:
    """
    Immediately kick a standby/sleeping drive awake via a small sector read.

    Returns once the drive has responded (or after a 60 s timeout).
    Safe to call on an already-active drive.
    """
    if not _DEVICE_RE.match(device):
        raise HTTPException(status_code=400, detail="Invalid device name")
    dev_path = f"/dev/{device}"
    logger.info("Spin-up requested for %s", dev_path)
    ready = wait_for_drive_active(dev_path, timeout=60, spin_up_on_standby=True)
    return JSONResponse({"device": dev_path, "ready": ready})


def run(host: str = "", port: int = 0) -> None:
    """
    Entry point for the backupd CLI command.

    Bind address/port come from BACKUPD_HOST/BACKUPD_PORT when not passed
    explicitly (default 0.0.0.0:9999).  Logs a loud warning if BACKUPD_TOKEN
    is unset, since the privileged endpoints will refuse to serve without it.
    """
    host = host or os.environ.get("BACKUPD_HOST", "0.0.0.0")
    port = port or int(os.environ.get("BACKUPD_PORT", "9999"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    if not _expected_token():
        logger.warning(
            "BACKUPD_TOKEN is not set — /status, /shutdown and /disk endpoints "
            "will return 503 until it is configured."
        )
    logger.info("Starting backupd on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    run()
