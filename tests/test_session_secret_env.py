"""Tests for resolving the session secret from HOZO_SESSION_SECRET.

The secret signs session cookies. It used to be generated on first boot and
written back into config.yaml — and that file gets copied into the config repo,
where the live secret was committed in plaintext. An environment variable cannot
travel that way by accident.
"""

import secrets
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
import yaml

from hozo.auth.session import ENV_SESSION_SECRET, resolve_session_secret

_VALID = secrets.token_hex(32)

_CONFIG_TEMPLATE = """settings:
  ssh_user: root
auth:
  rp_id: localhost
jobs: []
"""


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Guarantee the variable is unset, whatever the runner's environment holds."""
    monkeypatch.delenv(ENV_SESSION_SECRET, raising=False)
    yield


def _write_config(path: Path, session_secret: str = "") -> Path:
    raw = yaml.safe_load(_CONFIG_TEMPLATE)
    if session_secret:
        raw["auth"]["session_secret"] = session_secret
    path.write_text(yaml.dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _on_disk_secret(path: Path) -> str:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return str((raw.get("auth") or {}).get("session_secret", ""))


def _create_app(config_path: Path) -> object:
    """Build the app with the scheduler stubbed, as the API tests do."""
    from hozo.api.routes import create_app

    with (
        patch("hozo.scheduler.runner.HozoScheduler.start"),
        patch("hozo.scheduler.runner.HozoScheduler.stop"),
        patch("hozo.scheduler.runner.HozoScheduler.load_jobs_from_config", return_value=0),
    ):
        return create_app(config_path=str(config_path))


class TestResolveSessionSecret:
    """The resolver itself — pure, no filesystem."""

    def test_env_value_is_returned_and_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)

        secret, from_env = resolve_session_secret(None)

        assert secret == _VALID
        assert from_env is True

    def test_env_takes_precedence_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)
        config_secret = secrets.token_hex(32)

        secret, from_env = resolve_session_secret(config_secret)

        assert secret == _VALID
        assert secret != config_secret
        assert from_env is True

    def test_config_used_when_env_unset(self, no_env: None) -> None:
        config_secret = secrets.token_hex(32)

        secret, from_env = resolve_session_secret(config_secret)

        assert secret == config_secret
        assert from_env is False

    def test_generates_when_env_and_config_both_absent(self, no_env: None) -> None:
        secret, from_env = resolve_session_secret(None)

        assert len(secret) == 64
        int(secret, 16)  # raises if not hex
        assert from_env is False

    def test_whitespace_env_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, "   ")
        config_secret = secrets.token_hex(32)

        secret, from_env = resolve_session_secret(config_secret)

        assert secret == config_secret
        assert from_env is False

    def test_env_value_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A trailing newline from a heredoc or shell export is not a malformed value."""
        monkeypatch.setenv(ENV_SESSION_SECRET, f"  {_VALID}\n")

        secret, from_env = resolve_session_secret(None)

        assert secret == _VALID
        assert from_env is True

    def test_too_short_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, "abcd1234")

        with pytest.raises(ValueError, match=ENV_SESSION_SECRET):
            resolve_session_secret(None)

    def test_non_hex_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, "z" * 64)

        with pytest.raises(ValueError, match=ENV_SESSION_SECRET):
            resolve_session_secret(None)

    def test_error_does_not_echo_the_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The message reaches logs; the rejected value must not."""
        bad = "deadbeef" * 3
        monkeypatch.setenv(ENV_SESSION_SECRET, bad)

        with pytest.raises(ValueError) as exc:
            resolve_session_secret(None)

        assert bad not in str(exc.value)


class TestStartupWithEnvSecret:
    """The regression that matters: an env-supplied secret must not reach the YAML."""

    def test_startup_does_not_write_secret_to_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)
        cfg = _write_config(tmp_path / "config.yaml")

        app = _create_app(cfg)

        assert _on_disk_secret(cfg) == ""
        assert app.state.session_secret == _VALID  # type: ignore[attr-defined]

    def test_env_secret_stays_out_of_the_serialized_auth_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app.state.auth is written to disk verbatim by every save path.

        Keeping the env secret out of it is what makes the guarantee hold for
        save paths beyond startup — passkey registration, settings changes.
        """
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)
        cfg = _write_config(tmp_path / "config.yaml")

        app = _create_app(cfg)

        assert "session_secret" not in app.state.auth  # type: ignore[attr-defined]

    def test_env_secret_does_not_overwrite_an_existing_config_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stale config value is left alone — removing it is a separate action."""
        existing = secrets.token_hex(32)
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)
        cfg = _write_config(tmp_path / "config.yaml", session_secret=existing)

        app = _create_app(cfg)

        assert _on_disk_secret(cfg) == existing
        assert app.state.session_secret == _VALID  # type: ignore[attr-defined]

    def test_bootstrap_without_config_uses_env_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, _VALID)
        missing = tmp_path / "does-not-exist.yaml"

        app = _create_app(missing)

        assert app.state.session_secret == _VALID  # type: ignore[attr-defined]
        assert not missing.exists()

    def test_malformed_env_value_fails_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_SESSION_SECRET, "not-a-secret")
        cfg = _write_config(tmp_path / "config.yaml")

        with pytest.raises(ValueError, match=ENV_SESSION_SECRET):
            _create_app(cfg)


class TestStartupWithoutEnvSecret:
    """Existing deployments must be untouched."""

    def test_startup_generates_and_persists(self, tmp_path: Path, no_env: None) -> None:
        cfg = _write_config(tmp_path / "config.yaml")

        app = _create_app(cfg)

        written = _on_disk_secret(cfg)
        assert len(written) == 64
        assert app.state.session_secret == written  # type: ignore[attr-defined]

    def test_existing_config_secret_is_reused_not_regenerated(
        self, tmp_path: Path, no_env: None
    ) -> None:
        existing = secrets.token_hex(32)
        cfg = _write_config(tmp_path / "config.yaml", session_secret=existing)

        app = _create_app(cfg)

        assert app.state.session_secret == existing  # type: ignore[attr-defined]
        assert _on_disk_secret(cfg) == existing

    def test_bootstrap_without_config_satisfies_the_invariant(
        self, tmp_path: Path, no_env: None
    ) -> None:
        """The RuntimeError guard must stay satisfied on the no-config path."""
        app = _create_app(tmp_path / "does-not-exist.yaml")

        secret = app.state.session_secret  # type: ignore[attr-defined]
        assert secret
        assert app.state.auth["session_secret"] == secret  # type: ignore[attr-defined]
