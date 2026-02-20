"""WebAuthn registration and authentication helpers for Hōzō."""

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import webauthn
from webauthn.helpers import (
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

# Challenge expires after 5 minutes
CHALLENGE_TTL = 300


@dataclass
class StoredCredential:
    """A WebAuthn credential persisted in config.yaml."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    device_name: str
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": base64.urlsafe_b64encode(self.credential_id).decode().rstrip("="),
            "public_key": base64.urlsafe_b64encode(self.public_key).decode().rstrip("="),
            "sign_count": self.sign_count,
            "device_name": self.device_name,
            "added_at": self.added_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredCredential":
        return cls(
            credential_id=_b64url_decode(d["id"]),
            public_key=_b64url_decode(d["public_key"]),
            sign_count=int(d["sign_count"]),
            device_name=d.get("device_name", "Device"),
            added_at=datetime.fromisoformat(d["added_at"]),
        )


def _b64url_decode(s: str) -> bytes:
    """Decode a base64url string with or without padding."""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def begin_registration(
    rp_id: str,
    rp_name: str = "Hōzō",
) -> tuple[str, bytes]:
    """
    Begin WebAuthn registration.

    Returns:
        (options_json, challenge_bytes) — send options_json to the browser,
        store challenge_bytes in pending_challenges keyed by the challenge.
    """
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=b"admin",
        user_name="admin",
        user_display_name="Hōzō Admin",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return webauthn.options_to_json(options), options.challenge


def complete_registration(
    body: str,
    challenge: bytes,
    expected_rp_id: str,
    expected_origin: str,
    device_name: str = "Device",
) -> StoredCredential:
    """
    Complete WebAuthn registration and return a credential to store.

    Args:
        body: JSON string from the browser.
        challenge: The challenge bytes issued during begin_registration.
        expected_rp_id: RP ID (hostname, e.g. "mymachine.tailnet.ts.net").
        expected_origin: Full origin URL (e.g. "https://mymachine.tailnet.ts.net").
        device_name: Human-readable label chosen by the user.

    Returns:
        StoredCredential ready to be persisted.

    Raises:
        Exception: If verification fails (py_webauthn raises InvalidCBORData etc.)
    """
    credential = parse_registration_credential_json(body)
    verification = webauthn.verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        require_user_verification=True,
    )
    return StoredCredential(
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        device_name=device_name,
    )


def begin_authentication(
    rp_id: str,
    stored_credentials: list[StoredCredential],
) -> tuple[str, bytes]:
    """
    Begin WebAuthn authentication.

    Returns:
        (options_json, challenge_bytes) — send options_json to the browser,
        store challenge_bytes in pending_challenges.
    """
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=c.credential_id) for c in stored_credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return webauthn.options_to_json(options), options.challenge


def complete_authentication(
    body: str,
    challenge: bytes,
    expected_rp_id: str,
    expected_origin: str,
    stored_credentials: list[StoredCredential],
) -> StoredCredential:
    """
    Complete WebAuthn authentication.

    Returns:
        The matching StoredCredential with sign_count already updated.
        The caller must persist the updated sign_count to config.

    Raises:
        ValueError: If no matching credential is found.
        Exception: If verification fails.
    """
    credential = parse_authentication_credential_json(body)
    cred_id_bytes = _b64url_decode(credential.id)

    stored = next(
        (c for c in stored_credentials if c.credential_id == cred_id_bytes),
        None,
    )
    if stored is None:
        raise ValueError(f"No matching credential for id={credential.id!r}")

    verification = webauthn.verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=stored.public_key,
        credential_current_sign_count=stored.sign_count,
        require_user_verification=True,
    )
    stored.sign_count = verification.new_sign_count
    return stored


def store_challenge(
    pending: dict[str, tuple[bytes, float]],
    challenge: bytes,
) -> None:
    """
    Store a challenge in the pending dict and clean up expired entries.

    Args:
        pending: app.state.pending_challenges dict.
        challenge: Raw challenge bytes.
    """
    now = time.monotonic()
    # Prune expired challenges
    expired = [k for k, (_, exp) in pending.items() if now > exp]
    for k in expired:
        del pending[k]
    key = base64.urlsafe_b64encode(challenge).decode()
    pending[key] = (challenge, now + CHALLENGE_TTL)


def pop_challenge(
    pending: dict[str, tuple[bytes, float]],
    challenge: bytes,
) -> bytes:
    """
    Pop and validate a pending challenge.

    Returns:
        The challenge bytes if valid.

    Raises:
        ValueError: If challenge is missing or expired.
    """
    key = base64.urlsafe_b64encode(challenge).decode()
    entry = pending.pop(key, None)
    if entry is None:
        raise ValueError("Challenge not found or already used")
    _, expires_at = entry
    if time.monotonic() > expires_at:
        raise ValueError("Challenge expired")
    return challenge
