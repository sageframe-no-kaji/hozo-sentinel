"""Authentication helpers for Hōzō — session management (itsdangerous)."""

import os
import re
import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

COOKIE_NAME = "hozo_session"
SESSION_VALUE = "authenticated"
DEFAULT_MAX_AGE = 86400  # 24 hours

ENV_SESSION_SECRET = "HOZO_SESSION_SECRET"

# generate_secret() produces token_hex(32) — 64 hex characters. An env-supplied
# value must match that shape exactly; a truncated or mis-pasted one is a weak
# signing key and must fail at startup rather than quietly signing cookies.
_SECRET_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


def generate_secret() -> str:
    """Generate a cryptographically secure 32-byte hex secret for session signing."""
    return secrets.token_hex(32)


def resolve_session_secret(config_secret: str | None) -> tuple[str, bool]:
    """
    Resolve the session-signing secret, preferring the environment.

    Precedence: HOZO_SESSION_SECRET, then the config file, then a fresh secret.

    Args:
        config_secret: Value of auth.session_secret from config.yaml, if any.

    Returns:
        (secret, from_env). ``from_env`` tells the caller whether the secret came
        from the environment; when it did, the caller must not persist it to the
        config file. This function never touches the filesystem.

    Raises:
        ValueError: If HOZO_SESSION_SECRET is set but is not 64 hex characters.
    """
    env_secret = os.environ.get(ENV_SESSION_SECRET, "").strip()
    if env_secret:
        if not _SECRET_PATTERN.fullmatch(env_secret):
            # Deliberately does not echo the value — this message reaches logs.
            raise ValueError(
                f"{ENV_SESSION_SECRET} must be 64 hex characters "
                f"(got {len(env_secret)} characters). "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return env_secret, True

    if config_secret:
        return config_secret, False

    return generate_secret(), False


def make_session_cookie(secret: str) -> str:
    """
    Create a signed session cookie value.

    Args:
        secret: Hex secret from config (auth.session_secret).

    Returns:
        Signed string to set as the cookie value.
    """
    signer = TimestampSigner(secret)
    return signer.sign(SESSION_VALUE).decode()


def verify_session_cookie(cookie: str, secret: str, max_age: int = DEFAULT_MAX_AGE) -> bool:
    """
    Verify a session cookie.

    Args:
        cookie: Cookie value from the request.
        secret: Hex secret from config (auth.session_secret).
        max_age: Maximum age in seconds before the cookie is considered expired.

    Returns:
        True if the cookie is valid and not expired.
    """
    signer = TimestampSigner(secret)
    try:
        signer.unsign(cookie, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False
