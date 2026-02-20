"""Command-line interface for Hōzō (hozo)."""

import logging
import sys
from pathlib import Path
from typing import Optional

import click

from hozo import __version__

DEFAULT_CONFIG = Path.home() / ".config" / "hozo" / "config.yaml"


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_cfg(config: str) -> tuple[dict, list]:
    from hozo.config.loader import jobs_from_config, load_config, validate_config

    path = Path(config)
    if not path.exists():
        click.echo(f"Config file not found: {path}", err=True)
        sys.exit(1)
    raw = load_config(path)
    if not raw:
        click.echo("Config file is empty.", err=True)
        sys.exit(1)
    errors = validate_config(raw)
    if errors:
        click.echo("Config validation errors:", err=True)
        for e in errors:
            click.echo(f"  • {e}", err=True)
        sys.exit(1)
    return raw, jobs_from_config(raw)


# ── Root group ────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version=__version__, prog_name="hozo")
@click.option(
    "--config",
    "-c",
    default=str(DEFAULT_CONFIG),
    envvar="HOZO_CONFIG",
    show_default=True,
    help="Path to hozo config.yaml",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, config: str, verbose: bool) -> None:
    """Hōzō — Wake-on-demand ZFS backup orchestrator."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config


# ── jobs group ────────────────────────────────────────────────────────────────


@main.group()
def jobs() -> None:
    """Manage and execute backup jobs."""


@jobs.command("list")
@click.pass_context
def jobs_list(ctx: click.Context) -> None:
    """List all configured backup jobs."""
    _, job_objs = _load_cfg(ctx.obj["config"])
    if not job_objs:
        click.echo("No jobs configured.")
        return
    click.echo(f"{'NAME':<24} {'SOURCE':<24} {'TARGET HOST':<28} {'SHUTDOWN'}")
    click.echo("─" * 85)
    for j in job_objs:
        click.echo(
            f"{j.name:<24} {j.source_dataset:<24} {j.target_host:<28} "
            f"{'yes' if j.shutdown_after else 'no'}"
        )


@jobs.command("run")
@click.argument("job_name")
@click.pass_context
def jobs_run(ctx: click.Context, job_name: str) -> None:
    """Run a backup job immediately by name."""
    raw, job_objs = _load_cfg(ctx.obj["config"])
    match = next((j for j in job_objs if j.name == job_name), None)
    if match is None:
        click.echo(f"Job '{job_name}' not found in config.", err=True)
        sys.exit(1)

    click.echo(f"▶  Running job: {job_name}")
    from hozo.core.job import run_job
    from hozo.notifications.notify import send_notification

    result = run_job(match)
    send_notification(result, raw)

    if result.success:
        click.echo(
            f"✓  Job '{job_name}' completed in {result.duration_seconds:.1f}s "
            f"({len(result.snapshots_after)} remote snapshot(s))"
        )
    else:
        click.echo(f"✗  Job '{job_name}' failed: {result.error}", err=True)
        sys.exit(2)


# ── status command ────────────────────────────────────────────────────────────


@main.command()
@click.argument("target", default="remote")
@click.option("--job", "-j", help="Job name to infer the remote host from")
@click.pass_context
def status(ctx: click.Context, target: str, job: Optional[str]) -> None:
    """Show status of the remote backup host (resolves host from --job or first job)."""
    _, job_objs = _load_cfg(ctx.obj["config"])
    if not job_objs:
        click.echo("No jobs configured.")
        return

    chosen = job_objs[0]
    if job:
        found = next((j for j in job_objs if j.name == job), None)
        if not found:
            click.echo(f"Job '{job}' not found.", err=True)
            sys.exit(1)
        chosen = found

    from hozo.core.ssh import run_command, wait_for_ssh

    host = chosen.target_host
    click.echo(f"Checking SSH on {host}…")
    if not wait_for_ssh(host, port=chosen.ssh_port, timeout=10):
        click.echo(f"  SSH unreachable on {host}", err=True)
        return

    for cmd, label in [
        ("uptime", "Uptime"),
        ("zpool list", "ZPool list"),
        ("zpool status -x", "ZPool health"),
    ]:
        ec, out, err = run_command(host, cmd, user=chosen.ssh_user, key_path=chosen.ssh_key)
        click.echo(f"\n── {label} ──────────")
        click.echo(out.strip() if out.strip() else f"(exit {ec})")


# ── wake command ─────────────────────────────────────────────────────────────


@main.command()
@click.argument("job_name")
@click.pass_context
def wake(ctx: click.Context, job_name: str) -> None:
    """Send a Wake-on-LAN packet for the named job's host."""
    _, job_objs = _load_cfg(ctx.obj["config"])
    match = next((j for j in job_objs if j.name == job_name), None)
    if match is None:
        click.echo(f"Job '{job_name}' not found.", err=True)
        sys.exit(1)

    from hozo.core.wol import wake as do_wake

    do_wake(match.mac_address, ip_address=match.wol_broadcast)
    click.echo(f"WOL packet sent to {match.mac_address} ({match.target_host})")


# ── shutdown command ──────────────────────────────────────────────────────────


@main.command()
@click.argument("job_name")
@click.pass_context
def shutdown(ctx: click.Context, job_name: str) -> None:
    """SSH shutdown the remote host for the named job."""
    _, job_objs = _load_cfg(ctx.obj["config"])
    match = next((j for j in job_objs if j.name == job_name), None)
    if match is None:
        click.echo(f"Job '{job_name}' not found.", err=True)
        sys.exit(1)

    from hozo.core.ssh import run_command

    click.echo(f"Sending shutdown to {match.target_host}…")
    try:
        run_command(
            match.target_host,
            "shutdown -h now",
            user=match.ssh_user,
            port=match.ssh_port,
            key_path=match.ssh_key,
        )
        click.echo("Shutdown command sent.")
    except Exception as exc:
        click.echo(f"(Host may have already shut down: {exc})")


# ── serve command ─────────────────────────────────────────────────────────────


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, help="Bind port")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Start the Hōzō web UI and API server."""
    import uvicorn

    from hozo.api.routes import create_app

    config_path = ctx.obj["config"]
    app = create_app(config_path=config_path)
    click.echo(f"Starting Hōzō web UI at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
