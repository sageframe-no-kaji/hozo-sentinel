"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest
import yaml

from hozo.config.loader import jobs_from_config, load_config, validate_config


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        """Should load a valid YAML config file."""
        config_data = {
            "jobs": [
                {
                    "name": "test_backup",
                    "source": "rpool/data",
                    "target_host": "backup.local",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                }
            ]
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = load_config(config_file)

        assert result == config_data
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["name"] == "test_backup"

    def test_load_missing_file_raises(self) -> None:
        """Should raise FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.yaml"))

    def test_load_empty_file(self, tmp_path: Path) -> None:
        """Should return None for empty YAML file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        result = load_config(config_file)

        assert result is None

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """Should raise error for invalid YAML syntax."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: content: [")

        with pytest.raises(yaml.YAMLError):
            load_config(config_file)


class TestValidateConfig:
    """Tests for validate_config function."""

    def _minimal_job(self, **overrides: object) -> dict:
        base = {
            "name": "test",
            "source": "rpool/data",
            "target_host": "host",
            "target_dataset": "backup/data",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        }
        base.update(overrides)
        return base

    def test_valid_config_no_errors(self) -> None:
        config = {"jobs": [self._minimal_job()]}
        assert validate_config(config) == []

    def test_missing_jobs_key(self) -> None:
        errors = validate_config({})
        assert any("jobs" in e for e in errors)

    def test_invalid_mac(self) -> None:
        config = {"jobs": [self._minimal_job(mac_address="NOTAMAC")]}
        errors = validate_config(config)
        assert any("mac_address" in e for e in errors)

    def test_invalid_schedule(self) -> None:
        config = {"jobs": [self._minimal_job(schedule="every tuesday sometime")]}
        errors = validate_config(config)
        assert any("schedule" in e for e in errors)

    def test_valid_daily_schedule(self) -> None:
        config = {"jobs": [self._minimal_job(schedule="daily 03:00")]}
        assert validate_config(config) == []

    def test_valid_weekly_schedule(self) -> None:
        config = {"jobs": [self._minimal_job(schedule="weekly Sunday 02:30")]}
        assert validate_config(config) == []

    def test_missing_required_field(self) -> None:
        job = self._minimal_job()
        del job["target_host"]
        errors = validate_config({"jobs": [job]})
        assert any("target_host" in e for e in errors)


class TestJobsFromConfig:
    """Tests for jobs_from_config."""

    def test_creates_backup_job(self) -> None:
        config = {
            "jobs": [
                {
                    "name": "nightly",
                    "source": "rpool/data",
                    "target_host": "backup.local",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                }
            ]
        }
        jobs = jobs_from_config(config)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.name == "nightly"
        assert j.source_dataset == "rpool/data"
        assert j.shutdown_after is True
        assert j.timeout == 120  # default from settings

    def test_respects_global_settings(self) -> None:
        config = {
            "settings": {"ssh_timeout": 300, "ssh_user": "backup"},
            "jobs": [
                {
                    "name": "job",
                    "source": "tank/data",
                    "target_host": "host",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                }
            ],
        }
        jobs = jobs_from_config(config)
        assert jobs[0].timeout == 300
        assert jobs[0].ssh_user == "backup"
