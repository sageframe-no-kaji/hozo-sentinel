"""Tests for notification dispatchers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from hozo.core.job import JobResult
from hozo.notifications.notify import _build_body, _build_subject, send_notification


def _make_result(success: bool = True, error: str | None = None) -> JobResult:
    return JobResult(
        job_name="weekly",
        success=success,
        started_at=datetime(2024, 6, 1, 3, 0, 0),
        finished_at=datetime(2024, 6, 1, 3, 5, 30),
        error=error,
        snapshots_after=["backup/data@2024-06-01"],
    )


class TestBuildSubjectAndBody:
    def test_success_subject(self) -> None:
        subj = _build_subject(_make_result(success=True))
        assert "SUCCESS" in subj
        assert "weekly" in subj

    def test_failure_subject(self) -> None:
        subj = _build_subject(_make_result(success=False))
        assert "FAILED" in subj

    def test_body_contains_job_name(self) -> None:
        body = _build_body(_make_result())
        assert "weekly" in body

    def test_body_contains_error_when_present(self) -> None:
        body = _build_body(_make_result(success=False, error="syncoid crashed"))
        assert "syncoid crashed" in body

    def test_body_contains_duration(self) -> None:
        body = _build_body(_make_result())
        assert "330" in body or "5" in body  # 5.5 min or 330s


class TestSendNotification:
    def test_no_notifications_config_does_nothing(self) -> None:
        result = _make_result()
        # Should not raise even with empty config
        send_notification(result, {})

    @patch("hozo.notifications.notify.httpx.post")
    def test_ntfy_post_called_on_success(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        config = {"settings": {"notifications": {"ntfy_topic": "hozo-test"}}}
        send_notification(_make_result(success=True), config)
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "hozo-test" in url

    @patch("hozo.notifications.notify.httpx.post")
    def test_ntfy_high_priority_on_failure(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        config = {"settings": {"notifications": {"ntfy_topic": "hozo-alerts"}}}
        send_notification(_make_result(success=False), config)
        headers = mock_post.call_args[1]["headers"]
        assert headers["Priority"] == "high"

    @patch("hozo.notifications.notify.httpx.post")
    def test_pushover_post_called(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        config = {
            "settings": {
                "notifications": {
                    "pushover_token": "tok_abc",
                    "pushover_user": "usr_xyz",
                }
            }
        }
        send_notification(_make_result(), config)
        mock_post.assert_called_once()
        assert "pushover" in mock_post.call_args[0][0]

    @patch("hozo.notifications.notify.smtplib.SMTP")
    def test_email_not_sent_without_to_addr(self, mock_smtp: MagicMock) -> None:
        config = {
            "settings": {"notifications": {"smtp": {"host": "mail.example.com", "port": 587}}}
        }
        send_notification(_make_result(), config)
        mock_smtp.assert_not_called()
