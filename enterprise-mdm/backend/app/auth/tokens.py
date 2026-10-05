"""JWT access token and refresh token generation/validation.

Access token:
  - Short-lived (default 15 min), signed with HS256.
  - Claims: sub (user_id), session_id, token_type="access", iat, exp.
  - Does NOT contain roles/permissions: authorization is always re-checked
    server-side from the database to prevent stale permission escalation.

Refresh token:
  - cryptographically random URL-safe string (48 bytes → 64 chars base64url).
  - SHA-256 hash stored in the sessions table; raw token returned to client
    once and then discarded server-side.
  - Rotation: each use invalidates the old hash and issues a new pair.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Access token
# ---------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Create a signed JWT access token.

    Returns the encoded token string. NEVER log this value.
    """
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload = {
        "sub": str(user_id),
        "session_id": str(session_id),
        "token_type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, str]:
    """Decode and validate a JWT access token.

    Raises jwt.PyJWTError subclasses on any validation failure (expired,
    invalid signature, wrong type). Callers must handle these exceptions.
    """
    settings = get_settings()
    payload: dict[str, str] = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "session_id", "exp", "iat", "token_type"]},
    )
    if payload.get("token_type") != "access":
        raise jwt.InvalidTokenError("Not an access token.")
    return payload


# ---------------------------------------------------------------------------
# Refresh token
# ---------------------------------------------------------------------------


def generate_refresh_token() -> tuple[str, str]:
    """Generate a secure refresh token.

    Returns:
        (raw_token, token_hash) — store ONLY the hash in the database.
        Return the raw_token to the client (once).
    """
    raw = secrets.token_urlsafe(48)  # 48 bytes → 64-char URL-safe string
    token_hash = _hash_token(raw)
    return raw, token_hash


def hash_refresh_token(raw: str) -> str:
    """Return the SHA-256 hex digest used for database lookup."""
    return _hash_token(raw)


def _hash_token(raw: str) -> str:
    """SHA-256 hex digest. Used for both refresh tokens and reset tokens."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Password-reset / email-verification tokens (same generation pattern)
# ---------------------------------------------------------------------------


def generate_otp_token() -> tuple[str, str]:
    """Generate a secure single-use token for password reset / email verify.

    Returns (raw_token, token_hash). Same rules: store only the hash.
    """
    raw = secrets.token_urlsafe(32)  # 32 bytes → ~43-char URL-safe string
    return raw, _hash_token(raw)


def hash_otp_token(raw: str) -> str:
    return _hash_token(raw)
