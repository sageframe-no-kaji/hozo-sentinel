"""Tests for the APScheduler-based job scheduler."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

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
        from datetime import datetime, timezone

        from hozo.core.job import JobResult

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
