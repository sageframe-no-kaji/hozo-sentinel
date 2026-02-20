"""Tests for auth session and WebAuthn helpers."""

import time
from unittest.mock import MagicMock, patch

import pytest

from hozo.auth.session import generate_secret, make_session_cookie, verify_session_cookie
from hozo.auth.webauthn_helpers import (
    StoredCredential,
    _b64url_decode,
    begin_authentication,
    begin_registration,
    complete_authentication,
    complete_registration,
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


# ── WebAuthn library call coverage ───────────────────────────────────────────


class TestBeginRegistration:
    @patch("hozo.auth.webauthn_helpers.webauthn.generate_registration_options")
    @patch("hozo.auth.webauthn_helpers.webauthn.options_to_json")
    def test_returns_json_and_challenge(self, mock_to_json: MagicMock, mock_gen: MagicMock) -> None:
        mock_options = MagicMock()
        mock_options.challenge = b"\xab\xcd"
        mock_gen.return_value = mock_options
        mock_to_json.return_value = '{"publicKey": "test"}'

        result_json, challenge = begin_registration("localhost", "Hōzō")

        assert result_json == '{"publicKey": "test"}'
        assert challenge == b"\xab\xcd"
        mock_gen.assert_called_once()
        mock_to_json.assert_called_once_with(mock_options)

    @patch("hozo.auth.webauthn_helpers.webauthn.generate_registration_options")
    @patch("hozo.auth.webauthn_helpers.webauthn.options_to_json")
    def test_uses_rp_id_and_name(self, mock_to_json: MagicMock, mock_gen: MagicMock) -> None:
        mock_options = MagicMock()
        mock_options.challenge = b"\x01"
        mock_gen.return_value = mock_options
        mock_to_json.return_value = "{}"

        begin_registration("my.host.net", "MyApp")

        call_kwargs = mock_gen.call_args
        assert call_kwargs.kwargs["rp_id"] == "my.host.net"
        assert call_kwargs.kwargs["rp_name"] == "MyApp"


class TestCompleteRegistration:
    @patch("hozo.auth.webauthn_helpers.webauthn.verify_registration_response")
    @patch("hozo.auth.webauthn_helpers.parse_registration_credential_json")
    def test_returns_stored_credential(
        self, mock_parse: MagicMock, mock_verify: MagicMock
    ) -> None:
        mock_credential = MagicMock()
        mock_parse.return_value = mock_credential

        mock_verification = MagicMock()
        mock_verification.credential_id = b"\x10\x20\x30"
        mock_verification.credential_public_key = b"\x40\x50\x60"
        mock_verification.sign_count = 0
        mock_verify.return_value = mock_verification

        result = complete_registration(
            body='{"id":"abc"}',
            challenge=b"\xab\xcd",
            expected_rp_id="localhost",
            expected_origin="http://localhost",
            device_name="Security Key",
        )

        assert isinstance(result, StoredCredential)
        assert result.credential_id == b"\x10\x20\x30"
        assert result.public_key == b"\x40\x50\x60"
        assert result.sign_count == 0
        assert result.device_name == "Security Key"

    @patch("hozo.auth.webauthn_helpers.webauthn.verify_registration_response")
    @patch("hozo.auth.webauthn_helpers.parse_registration_credential_json")
    def test_raises_on_verification_failure(
        self, mock_parse: MagicMock, mock_verify: MagicMock
    ) -> None:
        mock_parse.return_value = MagicMock()
        mock_verify.side_effect = Exception("Invalid CBOR")

        with pytest.raises(Exception, match="Invalid CBOR"):
            complete_registration(
                body="{}",
                challenge=b"\x01",
                expected_rp_id="localhost",
                expected_origin="http://localhost",
            )


class TestBeginAuthentication:
    @patch("hozo.auth.webauthn_helpers.webauthn.generate_authentication_options")
    @patch("hozo.auth.webauthn_helpers.webauthn.options_to_json")
    def test_returns_json_and_challenge(self, mock_to_json: MagicMock, mock_gen: MagicMock) -> None:
        mock_options = MagicMock()
        mock_options.challenge = b"\xde\xad"
        mock_gen.return_value = mock_options
        mock_to_json.return_value = '{"allowCredentials": []}'

        cred = StoredCredential(
            credential_id=b"\x01\x02",
            public_key=b"\x03\x04",
            sign_count=1,
            device_name="Key",
        )
        result_json, challenge = begin_authentication("localhost", [cred])

        assert result_json == '{"allowCredentials": []}'
        assert challenge == b"\xde\xad"
        mock_gen.assert_called_once()

    @patch("hozo.auth.webauthn_helpers.webauthn.generate_authentication_options")
    @patch("hozo.auth.webauthn_helpers.webauthn.options_to_json")
    def test_no_credentials_still_works(self, mock_to_json: MagicMock, mock_gen: MagicMock) -> None:
        mock_options = MagicMock()
        mock_options.challenge = b"\x01"
        mock_gen.return_value = mock_options
        mock_to_json.return_value = "{}"

        begin_authentication("localhost", [])
        mock_gen.assert_called_once()


class TestCompleteAuthentication:
    def _make_stored_cred(self) -> StoredCredential:
        return StoredCredential(
            credential_id=b"\xAA\xBB\xCC\xDD",
            public_key=b"\x01\x02\x03\x04",
            sign_count=5,
            device_name="Test Key",
        )

    @patch("hozo.auth.webauthn_helpers.webauthn.verify_authentication_response")
    @patch("hozo.auth.webauthn_helpers.parse_authentication_credential_json")
    def test_returns_updated_credential(
        self, mock_parse: MagicMock, mock_verify: MagicMock
    ) -> None:
        import base64

        stored_cred = self._make_stored_cred()
        cred_id_b64 = base64.urlsafe_b64encode(stored_cred.credential_id).decode().rstrip("=")

        mock_credential = MagicMock()
        mock_credential.id = cred_id_b64
        mock_parse.return_value = mock_credential

        mock_verification = MagicMock()
        mock_verification.new_sign_count = 6
        mock_verify.return_value = mock_verification

        result = complete_authentication(
            body='{"id":"test"}',
            challenge=b"\x01\x02",
            expected_rp_id="localhost",
            expected_origin="http://localhost",
            stored_credentials=[stored_cred],
        )

        assert result.sign_count == 6
        assert result.credential_id == stored_cred.credential_id

    @patch("hozo.auth.webauthn_helpers.parse_authentication_credential_json")
    def test_raises_if_no_matching_credential(self, mock_parse: MagicMock) -> None:
        import base64

        # credential id that doesn't match any stored cred
        mock_credential = MagicMock()
        mock_credential.id = base64.urlsafe_b64encode(b"\xFF\xFF\xFF\xFF").decode().rstrip("=")
        mock_parse.return_value = mock_credential

        stored_cred = self._make_stored_cred()

        with pytest.raises(ValueError, match="No matching credential"):
            complete_authentication(
                body="{}",
                challenge=b"\x01",
                expected_rp_id="localhost",
                expected_origin="http://localhost",
                stored_credentials=[stored_cred],
            )

    @patch("hozo.auth.webauthn_helpers.webauthn.verify_authentication_response")
    @patch("hozo.auth.webauthn_helpers.parse_authentication_credential_json")
    def test_raises_on_verification_failure(
        self, mock_parse: MagicMock, mock_verify: MagicMock
    ) -> None:
        import base64

        stored_cred = self._make_stored_cred()
        cred_id_b64 = base64.urlsafe_b64encode(stored_cred.credential_id).decode().rstrip("=")

        mock_credential = MagicMock()
        mock_credential.id = cred_id_b64
        mock_parse.return_value = mock_credential
        mock_verify.side_effect = Exception("Signature mismatch")

        with pytest.raises(Exception, match="Signature mismatch"):
            complete_authentication(
                body="{}",
                challenge=b"\x01",
                expected_rp_id="localhost",
                expected_origin="http://localhost",
                stored_credentials=[stored_cred],
            )
