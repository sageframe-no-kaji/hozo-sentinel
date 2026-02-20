"""Tests for the APScheduler-based job scheduler."""

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hozo.core.job import BackupJob, JobResult
from hozo.scheduler.runner import HozoScheduler, parse_schedule


class TestParseSchedule:
    """Tests for parse_schedule."""

    def test_daily_schedule(self) -> None:
        trigger = parse_schedule("daily 03:00")
        fields = {f.name: f for f in trigger.fields}
        assert str(fields["hour"]) == "3"
        assert str(fields["minute"]) == "0"

    def test_weekly_schedule_sunday(self) -> None:
        trigger = parse_schedule("weekly Sunday 02:30")
        fields = {f.name: f for f in trigger.fields}
        assert str(fields["day_of_week"]) == "sun"
        assert str(fields["hour"]) == "2"
        assert str(fields["minute"]) == "30"

    def test_weekly_schedule_case_insensitive(self) -> None:
        trigger = parse_schedule("weekly monday 01:00")
        fields = {f.name: f for f in trigger.fields}
        assert str(fields["day_of_week"]) == "mon"

    def test_invalid_schedule_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_schedule("every tuesday at noon")

    def test_all_weekdays(self) -> None:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        abbrevs = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for day, abbrev in zip(days, abbrevs):
            trigger = parse_schedule(f"weekly {day} 00:00")
            fields = {f.name: f for f in trigger.fields}
            assert str(fields["day_of_week"]) == abbrev


class TestHozoScheduler:
    """Tests for HozoScheduler."""

    def _write_config(self, tmp_path: Path, schedule: str = "daily 03:00") -> Path:
        config = {
            "jobs": [
                {
                    "name": "test_job",
                    "source": "rpool/data",
                    "target_host": "backup.local",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "schedule": schedule,
                }
            ]
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(config))
        return path

    def test_load_jobs_registers_scheduled_job(self, tmp_path: Path) -> None:
        config_path = self._write_config(tmp_path)
        scheduler = HozoScheduler()
        count = scheduler.load_jobs_from_config(config_path)
        assert count == 1
        assert len(scheduler.jobs) == 1

    def test_job_names_loaded_correctly(self, tmp_path: Path) -> None:
        config_path = self._write_config(tmp_path)
        scheduler = HozoScheduler()
        scheduler.load_jobs_from_config(config_path)
        assert scheduler.jobs[0].name == "test_job"

    def test_no_schedule_job_not_registered(self, tmp_path: Path) -> None:
        """Jobs without a schedule key should not be added to APScheduler."""
        config = {
            "jobs": [
                {
                    "name": "manual_only",
                    "source": "rpool/data",
                    "target_host": "backup.local",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    # no schedule key
                }
            ]
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(config))

        scheduler = HozoScheduler()
        count = scheduler.load_jobs_from_config(path)
        assert count == 0  # nothing registered with APScheduler
        assert len(scheduler.jobs) == 1  # but job object IS loaded

    @patch("hozo.scheduler.runner.run_job")
    def test_on_result_callback_invoked(self, mock_run_job: MagicMock, tmp_path: Path) -> None:
        fake_result = JobResult(
            job_name="test_job", success=True, started_at=datetime.now(timezone.utc)
        )
        mock_run_job.return_value = fake_result

        callback = MagicMock()
        config_path = self._write_config(tmp_path)
        scheduler = HozoScheduler(on_result=callback)
        scheduler.load_jobs_from_config(config_path)

        # Manually trigger the wrapper
        scheduler._run_job_wrapper(scheduler.jobs[0])

        callback.assert_called_once_with(fake_result)


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — real scheduler, real APScheduler background thread
# ─────────────────────────────────────────────────────────────────────────────


class TestSchedulerIntegration:
    """
    Integration tests that start a real BackgroundScheduler and verify jobs
    actually fire.

    ``run_job`` is patched so no real SSH / WOL / syncoid calls are made, but
    the full APScheduler lifecycle (start → schedule → fire → callback → stop)
    is exercised without mocking.
    """

    def _make_job(self) -> BackupJob:
        return BackupJob(
            name="integration_test_job",
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
            mac_address="AA:BB:CC:DD:EE:FF",
        )

    @patch("hozo.scheduler.runner.run_job")
    def test_scheduled_job_actually_fires(self, mock_run_job: MagicMock) -> None:
        """
        Schedule a job to fire 1 second from now using DateTrigger.
        Verify the job function and the on_result callback are both invoked.
        """
        from apscheduler.triggers.date import DateTrigger

        fake_result = JobResult(
            job_name="integration_test_job",
            success=True,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        mock_run_job.return_value = fake_result

        fired_event = threading.Event()
        received_results: list[JobResult] = []

        def on_result(result: JobResult) -> None:
            received_results.append(result)
            fired_event.set()

        scheduler = HozoScheduler(on_result=on_result)
        job = self._make_job()

        # Schedule to fire 1 second from now
        fire_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        scheduler.schedule_job(job, DateTrigger(run_date=fire_at, timezone="UTC"))
        scheduler.start()

        try:
            fired = fired_event.wait(timeout=10)
        finally:
            scheduler.stop(wait=False)

        assert fired, "Job did not fire within 10 seconds — scheduler is not working"
        assert mock_run_job.called, "run_job was never called by the scheduler"
        assert len(received_results) == 1
        assert received_results[0].job_name == "integration_test_job"
        assert received_results[0].success is True

    @patch("hozo.scheduler.runner.run_job")
    def test_job_fires_multiple_times_with_interval(self, mock_run_job: MagicMock) -> None:
        """
        Use an IntervalTrigger every 0.5 s and verify the job fires at least twice,
        proving the scheduler keeps running between invocations.
        """
        from apscheduler.triggers.interval import IntervalTrigger

        call_count = 0
        reached_two = threading.Event()

        def fake_run_job(job: BackupJob) -> JobResult:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                reached_two.set()
            return JobResult(
                job_name=job.name,
                success=True,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )

        mock_run_job.side_effect = fake_run_job

        scheduler = HozoScheduler()
        job = self._make_job()
        scheduler.schedule_job(job, IntervalTrigger(seconds=0.5))
        scheduler.start()

        try:
            fired_twice = reached_two.wait(timeout=10)
        finally:
            scheduler.stop(wait=False)

        assert (
            fired_twice
        ), f"Job only fired {call_count} times in 10 s — scheduler may have stopped early"
        assert call_count >= 2

    @patch("hozo.scheduler.runner.run_job")
    def test_stop_prevents_further_fires(self, mock_run_job: MagicMock) -> None:
        """
        After stop() is called no new job executions should occur.
        """
        from apscheduler.triggers.interval import IntervalTrigger

        fired_after_stop = threading.Event()
        stopped = threading.Event()
        call_count = 0

        def fake_run_job(job: BackupJob) -> JobResult:
            nonlocal call_count
            call_count += 1
            if stopped.is_set():
                fired_after_stop.set()
            return JobResult(
                job_name=job.name,
                success=True,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )

        mock_run_job.side_effect = fake_run_job

        scheduler = HozoScheduler()
        job = self._make_job()
        scheduler.schedule_job(job, IntervalTrigger(seconds=0.2))
        scheduler.start()

        # Let it fire at least once
        time.sleep(0.5)
        scheduler.stop(wait=True)
        stopped.set()

        # Wait briefly — any rogue fire would set fired_after_stop
        time.sleep(0.5)

        assert not fired_after_stop.is_set(), "Job fired after scheduler was stopped"
        assert call_count >= 1, "Job should have fired at least once before stop"


# ── Additional coverage ───────────────────────────────────────────────────────


class TestSchedulerEdgeCases:
    """Cover branches missed by the main tests."""

    def _write_config(self, tmp_path: Path, jobs: list) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"jobs": jobs}))
        return path

    def test_load_empty_config_returns_zero(self, tmp_path: Path) -> None:
        """Empty YAML file → load_jobs_from_config returns 0."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        scheduler = HozoScheduler()
        count = scheduler.load_jobs_from_config(path)
        assert count == 0

    def test_invalid_schedule_logs_error_and_continues(self, tmp_path: Path) -> None:
        """A job with an unparseable schedule is silently skipped (not registered)."""
        path = self._write_config(
            tmp_path,
            [
                {
                    "name": "bad_sched",
                    "source": "rpool/data",
                    "target_host": "host",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "schedule": "every tuesday at noon",
                }
            ],
        )
        scheduler = HozoScheduler()
        count = scheduler.load_jobs_from_config(path)
        assert count == 0           # not registered with APScheduler
        assert len(scheduler.jobs) == 1  # but job object IS in the list

    @patch("hozo.scheduler.runner.run_job")
    def test_on_result_callback_exception_is_swallowed(
        self, mock_run_job: MagicMock, tmp_path: Path
    ) -> None:
        """If on_result raises, the wrapper must not propagate the exception."""
        fake_result = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
        )
        mock_run_job.return_value = fake_result

        def bad_callback(r: JobResult) -> None:
            raise RuntimeError("callback crashed")

        scheduler = HozoScheduler(on_result=bad_callback)
        path = self._write_config(
            tmp_path,
            [
                {
                    "name": "weekly",
                    "source": "rpool/data",
                    "target_host": "host",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "schedule": "daily 03:00",
                }
            ],
        )
        scheduler.load_jobs_from_config(path)
        # Must not raise even though callback raises
        scheduler._run_job_wrapper(scheduler.jobs[0])

    def test_run_job_now_unknown_name_returns_none(self, tmp_path: Path) -> None:
        """run_job_now for a nonexistent job returns None."""
        path = self._write_config(
            tmp_path,
            [
                {
                    "name": "weekly",
                    "source": "rpool/data",
                    "target_host": "host",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "schedule": "daily 03:00",
                }
            ],
        )
        scheduler = HozoScheduler()
        scheduler.load_jobs_from_config(path)
        result = scheduler.run_job_now("does_not_exist")
        assert result is None

    @patch("hozo.scheduler.runner.run_job")
    def test_run_job_now_known_name_returns_result(
        self, mock_run_job: MagicMock, tmp_path: Path
    ) -> None:
        """run_job_now with a valid name calls run_job and returns the result."""
        fake_result = JobResult(
            job_name="weekly",
            success=True,
            started_at=datetime.now(timezone.utc),
        )
        mock_run_job.return_value = fake_result

        path = self._write_config(
            tmp_path,
            [
                {
                    "name": "weekly",
                    "source": "rpool/data",
                    "target_host": "host",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "schedule": "daily 03:00",
                }
            ],
        )
        scheduler = HozoScheduler()
        scheduler.load_jobs_from_config(path)
        result = scheduler.run_job_now("weekly")
        assert result is not None
        assert result.job_name == "weekly"
        mock_run_job.assert_called_once()
