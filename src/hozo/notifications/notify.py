"""Notification dispatchers for job results (ntfy.sh, Pushover, email)."""

import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any, Optional

import httpx

from hozo.core.job import JobResult

logger = logging.getLogger(__name__)


def send_notification(result: JobResult, config: dict[str, Any]) -> None:
    """
    Send a notification based on job result and config notification settings.

    Dispatches to all configured notification channels:
      - ntfy.sh (if notifications.ntfy_topic is set)
      - Pushover (if notifications.pushover_token + pushover_user are set)
      - Email / SMTP (if notifications.smtp is configured)

    Args:
        result: The JobResult from a completed backup job
        config: Full config dict (reads config["settings"]["notifications"])
    """
    notif_config = config.get("settings", {}).get("notifications", {})
    if not notif_config:
        return

    subject = _build_subject(result)
    body = _build_body(result)

    ntfy_topic = notif_config.get("ntfy_topic")
    if ntfy_topic:
        _send_ntfy(topic=ntfy_topic, title=subject, message=body, success=result.success)

    pushover_token = notif_config.get("pushover_token")
    pushover_user = notif_config.get("pushover_user")
    if pushover_token and pushover_user:
        _send_pushover(token=pushover_token, user=pushover_user, title=subject, message=body)

    smtp_cfg = notif_config.get("smtp")
    if smtp_cfg:
        _send_email(smtp_cfg=smtp_cfg, subject=subject, body=body)


# ── Formatters ────────────────────────────────────────────────────────────────


def _build_subject(result: JobResult) -> str:
    status = "✅ SUCCESS" if result.success else "❌ FAILED"
    return f"Hōzō {status}: {result.job_name}"


def _build_body(result: JobResult) -> str:
    lines = [
        f"Job: {result.job_name}",
        f"Status: {'Success' if result.success else 'Failed'}",
        f"Started: {result.started_at.isoformat()}",
    ]
    if result.finished_at:
        lines.append(f"Finished: {result.finished_at.isoformat()}")
    if result.duration_seconds is not None:
        lines.append(f"Duration: {result.duration_seconds:.1f}s")
    if result.attempts > 1:
        lines.append(f"Attempts: {result.attempts}")
    if result.snapshots_after:
        lines.append(f"Snapshots on remote: {len(result.snapshots_after)}")
        lines.append(f"Latest: {result.snapshots_after[-1]}")
    if result.error:
        lines.append(f"Error: {result.error}")
    return "\n".join(lines)


# ── ntfy.sh ───────────────────────────────────────────────────────────────────


def _send_ntfy(
    topic: str,
    title: str,
    message: str,
    success: bool,
    server: str = "https://ntfy.sh",
) -> None:
    url = f"{server.rstrip('/')}/{topic}"
    priority = "default" if success else "high"
    tags = "white_check_mark" if success else "warning"
    try:
        resp = httpx.post(
            url,
            content=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("ntfy notification sent to topic '%s'", topic)
    except Exception as exc:
        logger.error("Failed to send ntfy notification: %s", exc)


# ── Pushover ──────────────────────────────────────────────────────────────────


def _send_pushover(token: str, user: str, title: str, message: str) -> None:
    try:
        resp = httpx.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": token, "user": user, "title": title, "message": message},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Pushover notification sent")
    except Exception as exc:
        logger.error("Failed to send Pushover notification: %s", exc)


# ── Email / SMTP ──────────────────────────────────────────────────────────────


def _send_email(smtp_cfg: dict[str, Any], subject: str, body: str) -> None:
    """
    smtp_cfg keys: host, port, user, password, from_addr, to_addr, use_tls
    """
    host: str = smtp_cfg.get("host", "localhost")
    port: int = int(smtp_cfg.get("port", 587))
    user: Optional[str] = smtp_cfg.get("user")
    password: Optional[str] = smtp_cfg.get("password")
    from_addr: str = smtp_cfg.get("from_addr", user or "hozo@localhost")
    to_addr: str = smtp_cfg.get("to_addr", "")
    use_tls: bool = bool(smtp_cfg.get("use_tls", True))

    if not to_addr:
        logger.warning("SMTP configured but no 'to_addr' specified — skipping email")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("Email notification sent to %s", to_addr)
    except Exception as exc:
        logger.error("Failed to send email notification: %s", exc)
