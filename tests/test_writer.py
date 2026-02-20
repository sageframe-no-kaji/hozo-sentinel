"""Tests for the YAML config writer."""

from pathlib import Path

from hozo.config.loader import load_config
from hozo.config.writer import build_config_dict, job_to_raw, write_config
from hozo.core.job import BackupJob


def _make_job(**kwargs) -> BackupJob:
    defaults = dict(
        name="test_job",
        source_dataset="rpool/data",
        target_host="backup.local",
        target_dataset="backup/data",
        mac_address="AA:BB:CC:DD:EE:FF",
        schedule="weekly Sunday 03:00",
    )
    defaults.update(kwargs)
    return BackupJob(**defaults)


class TestJobToRaw:
    def test_required_fields_present(self) -> None:
        raw = job_to_raw(_make_job())
        for key in ("name", "source", "target_host", "target_dataset", "mac_address"):
            assert key in raw

    def test_source_maps_to_source_dataset(self) -> None:
        raw = job_to_raw(_make_job(source_dataset="rpool/photos"))
        assert raw["source"] == "rpool/photos"

    def test_schedule_included_when_set(self) -> None:
        raw = job_to_raw(_make_job(schedule="daily 02:00"))
        assert raw["schedule"] == "daily 02:00"

    def test_schedule_omitted_when_empty(self) -> None:
        raw = job_to_raw(_make_job(schedule=""))
        assert "schedule" not in raw

    def test_ssh_key_omitted_when_none(self) -> None:
        raw = job_to_raw(_make_job(ssh_key=None))
        assert "ssh_key" not in raw

    def test_ssh_key_included_when_set(self) -> None:
        raw = job_to_raw(_make_job(ssh_key="/root/.ssh/id_ed25519"))
        assert raw["ssh_key"] == "/root/.ssh/id_ed25519"

    def test_backup_device_omitted_when_none(self) -> None:
        raw = job_to_raw(_make_job(backup_device=None))
        assert "backup_device" not in raw

    def test_backup_device_included_with_timeout(self) -> None:
        raw = job_to_raw(_make_job(backup_device="/dev/sdb", disk_spinup_timeout=120))
        assert raw["backup_device"] == "/dev/sdb"
        assert raw["disk_spinup_timeout"] == 120


class TestWriteConfig:
    def test_write_and_reload(self, tmp_path: Path) -> None:
        cfg = {
            "jobs": [
                {
                    "name": "test",
                    "source": "rpool/data",
                    "target_host": "h",
                    "target_dataset": "b/d",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                }
            ]
        }
        p = tmp_path / "config.yaml"
        write_config(p, cfg)
        loaded = load_config(p)
        assert loaded is not None
        assert loaded["jobs"][0]["name"] == "test"

    def test_atomic_write_creates_no_tmp_on_success(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        write_config(p, {"jobs": []})
        assert not (tmp_path / "config.yaml.tmp").exists()

    def test_write_preserves_unicode(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        write_config(p, {"jobs": [], "description": "宝蔵"})
        text = p.read_text(encoding="utf-8")
        assert "宝蔵" in text


class TestBuildConfigDict:
    def test_structure(self) -> None:
        jobs = [_make_job()]
        settings = {"ssh_timeout": 60, "ssh_user": "admin"}
        auth = {"session_secret": "abc", "credentials": []}
        cfg = build_config_dict(jobs, settings=settings, auth=auth)
        assert "jobs" in cfg
        assert "settings" in cfg
        assert "auth" in cfg
        assert cfg["settings"]["ssh_timeout"] == 60

    def test_no_settings_or_auth_omitted(self) -> None:
        cfg = build_config_dict([_make_job()])
        assert "settings" not in cfg
        assert "auth" not in cfg
        assert len(cfg["jobs"]) == 1

    def test_roundtrip_through_yaml(self, tmp_path: Path) -> None:
        jobs = [_make_job(schedule="daily 04:00")]
        cfg = build_config_dict(jobs)
        p = tmp_path / "config.yaml"
        write_config(p, cfg)
        loaded = load_config(p)
        assert loaded is not None
        assert loaded["jobs"][0]["schedule"] == "daily 04:00"
