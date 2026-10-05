"""Cryptographic helpers for enrollment tokens and device credentials.

Same pattern as app/auth/tokens.py's refresh/reset tokens and
OrganizationInvitation.token_hash: generate a cryptographically random raw
value, return it to the caller exactly once, persist only its SHA-256
hash. Neither an enrollment token nor a device secret is ever stored or
logged in the clear.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_enrollment_token() -> tuple[str, str]:
    """Returns (raw_token, token_hash). Store only the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_device_secret() -> tuple[str, str]:
    """Returns (raw_secret, secret_hash) — the per-device bearer credential
    issued once at enrollment completion. Store only the hash on Device."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
