"""Authentication helpers for Hōzō — session management (itsdangerous)."""

import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

COOKIE_NAME = "hozo_session"
SESSION_VALUE = "authenticated"
DEFAULT_MAX_AGE = 86400  # 24 hours


def generate_secret() -> str:
    """Generate a cryptographically secure 32-byte hex secret for session signing."""
    return secrets.token_hex(32)


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
