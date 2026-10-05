"""Hashing helpers for sensitive device identifiers (IMEI, Wi-Fi MAC).

Same trust model as OrganizationInvitation.token_hash and refresh-token
hashing (app/auth/tokens.py): a deterministic SHA-256 digest is stored for
lookup/deduplication, the raw value is never persisted, and — unlike a
password hash — this is intentionally NOT a slow/salted KDF, because the
whole point is a stable, comparable digest for uniqueness checks.

There is deliberately no reverse path. Phase 4 has no genuine requirement
to ever display a device's full IMEI or MAC address again after creation,
so reversible encryption is not used (see docs/security/device-security.md).
Only the last few characters are retained in the clear, for the caller to
visually confirm which physical device a record refers to.
"""

from __future__ import annotations

import hashlib
import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def normalize_identifier(raw: str) -> str:
    """Strip separators (spaces, dashes, colons) and uppercase.

    "aa:bb:cc:dd:ee:ff" -> "AABBCCDDEEFF". Ensures the same physical
    identifier hashes identically regardless of how a caller formatted it.
    """
    return _NON_ALNUM.sub("", raw).upper()


def hash_identifier(raw: str) -> str:
    """SHA-256 hex digest of the normalized identifier. Deterministic —
    used for organization-scoped deduplication, never for secrecy alone."""
    return hashlib.sha256(normalize_identifier(raw).encode()).hexdigest()


def last_chars(raw: str, n: int = 4) -> str:
    """Last *n* characters of the normalized identifier, for display only."""
    normalized = normalize_identifier(raw)
    return normalized[-n:] if normalized else ""
