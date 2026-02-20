"""SSH connectivity and remote command execution."""

import logging
import socket
import time
from pathlib import Path
from typing import Optional

import paramiko

logger = logging.getLogger(__name__)


def wait_for_ssh(
    host: str,
    port: int = 22,
    timeout: int = 120,
    poll_interval: float = 5.0,
) -> bool:
    """
    Wait for SSH to become available on a remote host by polling TCP port 22.

    Args:
        host: Hostname or IP address
        port: SSH port (default: 22)
        timeout: Maximum seconds to wait (default: 120)
        poll_interval: Seconds between retries (default: 5)

    Returns:
        True if SSH is available within the timeout window, False otherwise
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with socket.create_connection((host, port), timeout=5):
                logger.info("SSH available on %s:%d (attempt %d)", host, port, attempt)
                return True
        except (OSError, socket.timeout):
            remaining = deadline - time.monotonic()
            logger.debug(
                "SSH not yet available on %s:%d — %.0f s remaining (attempt %d)",
                host,
                port,
                max(0, remaining),
                attempt,
            )
            if remaining > poll_interval:
                time.sleep(poll_interval)
            else:
                time.sleep(max(0, remaining))
    logger.warning("SSH did not become available on %s:%d within %d s", host, port, timeout)
    return False


def run_command(
    host: str,
    command: str,
    user: str = "root",
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    connect_timeout: int = 30,
) -> tuple[int, str, str]:
    """
    Execute a command on a remote host via SSH.

    Args:
        host: Hostname or IP address
        command: Command to execute
        user: SSH user (default: root)
        port: SSH port (default: 22)
        key_path: Path to SSH private key (uses default key if None)
        password: SSH password (not recommended; prefer key auth)
        connect_timeout: Connection timeout in seconds (default: 30)

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": connect_timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }
    if key_path:
        connect_kwargs["key_filename"] = str(Path(key_path).expanduser())
    if password:
        connect_kwargs["password"] = password

    logger.debug("SSH connect %s@%s:%d — running: %s", user, host, port, command)
    try:
        client.connect(**connect_kwargs)
        stdin, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_code != 0:
            logger.warning("Remote command returned exit code %d: %s", exit_code, err.strip())
        return exit_code, out, err
    finally:
        client.close()
