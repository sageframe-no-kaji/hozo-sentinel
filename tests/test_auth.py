"""Tests for auth session and WebAuthn helpers."""

import time

import pytest

from hozo.auth.session import generate_secret, make_session_cookie, verify_session_cookie
from hozo.auth.webauthn_helpers import (
    StoredCredential,
    _b64url_decode,
    pop_challenge,
    store_challenge,
)

# ── Session tests ─────────────────────────────────────────────────────────────


class TestGenerateSecret:
    def test_returns_64_char_hex(self) -> None:
        s = generate_secret()
        assert len(s) == 64
        assert all(c in "0123456789abcdef" for c in s)

    def test_each_call_unique(self) -> None:
        assert generate_secret() != generate_secret()


class TestSessionCookie:
    def test_roundtrip(self) -> None:
        secret = generate_secret()
        cookie = make_session_cookie(secret)
        assert verify_session_cookie(cookie, secret)

    def test_wrong_secret_fails(self) -> None:
        cookie = make_session_cookie(generate_secret())
        assert not verify_session_cookie(cookie, generate_secret())

    def test_tampered_cookie_fails(self) -> None:
        secret = generate_secret()
        cookie = make_session_cookie(secret)
        assert not verify_session_cookie(cookie + "x", secret)

    def test_expired_cookie_fails(self) -> None:
        secret = generate_secret()
        cookie = make_session_cookie(secret)
        # max_age=-1 → always expired (0 fails due to 1-second timestamp granularity)
        assert not verify_session_cookie(cookie, secret, max_age=-1)

    def test_empty_cookie_fails(self) -> None:
        assert not verify_session_cookie("", generate_secret())


# ── WebAuthn helpers tests ────────────────────────────────────────────────────


class TestB64UrlDecode:
    def test_decode_with_padding(self) -> None:
        # base64url for b"hello"
        assert _b64url_decode("aGVsbG8=") == b"hello"

    def test_decode_without_padding(self) -> None:
        assert _b64url_decode("aGVsbG8") == b"hello"


class TestStoredCredential:
    def _make_cred(self) -> StoredCredential:
        return StoredCredential(
            credential_id=b"\x01\x02\x03\x04",
            public_key=b"\x05\x06\x07\x08",
            sign_count=42,
            device_name="Test Device",
        )

    def test_to_dict_contains_expected_keys(self) -> None:
        d = self._make_cred().to_dict()
        assert set(d.keys()) == {"id", "public_key", "sign_count", "device_name", "added_at"}

    def test_roundtrip(self) -> None:
        cred = self._make_cred()
        restored = StoredCredential.from_dict(cred.to_dict())
        assert restored.credential_id == cred.credential_id
        assert restored.public_key == cred.public_key
        assert restored.sign_count == cred.sign_count
        assert restored.device_name == cred.device_name

    def test_id_is_base64url_no_padding(self) -> None:
        d = self._make_cred().to_dict()
        assert "=" not in d["id"]


class TestChallengeStore:
    def test_store_and_pop(self) -> None:
        pending: dict = {}
        challenge = b"\xab\xcd\xef"
        store_challenge(pending, challenge)
        assert len(pending) == 1
        result = pop_challenge(pending, challenge)
        assert result == challenge
        assert len(pending) == 0  # consumed

    def test_pop_unknown_raises(self) -> None:
        pending: dict = {}
        with pytest.raises(ValueError, match="not found"):
            pop_challenge(pending, b"\x00\x01")

    def test_pop_twice_raises(self) -> None:
        pending: dict = {}
        challenge = b"\x01\x02"
        store_challenge(pending, challenge)
        pop_challenge(pending, challenge)
        with pytest.raises(ValueError):
            pop_challenge(pending, challenge)

    def test_expired_challenge_pruned(self) -> None:
        pending: dict = {}
        challenge = b"\x03\x04"
        store_challenge(pending, challenge)
        # Force expiry by setting timestamp to the past
        key = list(pending.keys())[0]
        pending[key] = (pending[key][0], time.monotonic() - 1)
        with pytest.raises(ValueError, match="expired"):
            pop_challenge(pending, challenge)

    def test_old_challenges_pruned_on_new_store(self) -> None:
        pending: dict = {}
        old_challenge = b"\x11"
        store_challenge(pending, old_challenge)
        key = list(pending.keys())[0]
        pending[key] = (pending[key][0], time.monotonic() - 1000)  # very old
        new_challenge = b"\x22"
        store_challenge(pending, new_challenge)
        # Old challenge should be pruned
        assert len(pending) == 1
