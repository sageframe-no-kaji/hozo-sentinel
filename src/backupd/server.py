"""
backupd — lightweight HTTP agent for the Hōzō remote backup box.

Exposes:
    GET  /ping      → liveness probe
    GET  /status    → ZFS pool health + uptime
    POST /shutdown  → safe shutdown (export pools, then power off)

Deploy on the remote backup machine as a systemd service::

    [Unit]
    Description=Hōzō backup agent
    After=network.target

    [Service]
    ExecStart=/usr/local/bin/backupd
    Restart=always

    [Install]
    WantedBy=multi-user.target
"""

import logging
import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backupd.disk import drive_summary, wait_for_drive_active
from backupd.system import get_uptime, safe_shutdown
from backupd.zfs import get_pool_status, list_pools

logger = logging.getLogger(__name__)

app = FastAPI(title="backupd", version="0.1.0", description="Hōzō remote backup agent")


@app.get("/ping")
async def ping() -> dict[str, Any]:
    """Liveness probe — returns immediately."""
    return {"status": "ok", "uptime": get_uptime()}


@app.get("/status")
async def status() -> dict[str, Any]:
    """Return ZFS pool health, pool list, and system uptime."""
    pools = list_pools()
    pool_states = get_pool_status()
    return {
        "uptime_seconds": get_uptime(),
        "pools": pools,
        "pool_states": pool_states,
    }


@app.post("/shutdown")
async def shutdown_endpoint(request: Request) -> JSONResponse:
    """
    Initiate a safe shutdown.  Exports ZFS pools then powers off.
    The HTTP response is sent before the machine goes down.
    """
    logger.info("Shutdown request received from %s", request.client)
    # Run the actual shutdown in a background thread so the HTTP response
    # can be returned before the process is killed.
    t = threading.Thread(target=safe_shutdown, kwargs={"export_pools": True, "delay_seconds": 2})
    t.daemon = True
    t.start()
    return JSONResponse({"status": "shutdown_initiated"})


@app.get("/disk/{device}")
async def disk_status(device: str) -> dict[str, Any]:
    """
    Return full drive summary for a device (e.g., ``/dev/sda`` → device=``sda``).

    Response includes ``state``, ``active`` (bool), and ``io_completions``.
    """
    dev_path = f"/dev/{device}"
    return drive_summary(dev_path)


@app.post("/disk/{device}/spinup")
async def disk_spinup(device: str) -> JSONResponse:
    """
    Immediately kick a standby/sleeping drive awake via a small sector read.

    Returns once the drive has responded (or after a 60 s timeout).
    Safe to call on an already-active drive.
    """
    dev_path = f"/dev/{device}"
    logger.info("Spin-up requested for %s", dev_path)
    ready = wait_for_drive_active(dev_path, timeout=60, spin_up_on_standby=True)
    return JSONResponse({"device": dev_path, "ready": ready})


def run(host: str = "0.0.0.0", port: int = 9999) -> None:
    """Entry point for the backupd CLI command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info("Starting backupd on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
