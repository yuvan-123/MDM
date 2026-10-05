"""Password hashing and policy enforcement.

Algorithm: Argon2id via passlib — the recommended modern password hashing
algorithm per NIST SP 800-63B, OWASP, and RFC 9106.

NEVER use this module's output in log messages, API responses, or any
storage other than the users.password_hash column.
"""

from __future__ import annotations

from passlib.context import CryptContext  # type: ignore[import-untyped]

from app.core.config import get_settings

# Argon2id context — time_cost=2, memory_cost=65536 (64MB), parallelism=2
# aligns with OWASP recommended Argon2id minimums for interactive login.
# These are stored in the hash string itself so they can be tuned without
# a full re-hash cycle.
_pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__type="ID",
    argon2__time_cost=2,
    argon2__memory_cost=65536,
    argon2__parallelism=2,
)


def hash_password(plain: str) -> str:
    """Return an Argon2id hash of *plain*.

    The result is safe to store in the database.
    NEVER log *plain* or the returned hash.
    """
    return str(_pwd_context.hash(plain))


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*.

    Uses constant-time comparison internally. Safe against timing attacks.
    """
    return bool(_pwd_context.verify(plain, hashed))


def needs_rehash(hashed: str) -> bool:
    """Return True if *hashed* was created with outdated parameters.

    Call after a successful verify_password(); if True, re-hash and update
    the stored hash while the plaintext is still available.
    """
    return bool(_pwd_context.needs_update(hashed))


def check_password_policy(plain: str) -> list[str]:
    """Validate *plain* against the configured password policy.

    Returns a list of human-readable violation strings (empty = valid).

    Policy (NIST SP 800-63B aligned):
      - Minimum length (configurable, default 12)
      - Maximum length (128 — protects against DoS via huge hash inputs)
      - Not all whitespace

    Deliberately does NOT require: uppercase, numbers, special characters.
    Complexity requirements are not recommended by NIST and create poor UX
    without measurable security benefit when length + uniqueness checking
    are in place.
    """
    settings = get_settings()
    violations: list[str] = []

    if len(plain) < settings.password_min_length:
        violations.append(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if len(plain) > settings.password_max_length:
        violations.append(
            f"Password must not exceed {settings.password_max_length} characters."
        )
    if plain.strip() == "":
        violations.append("Password must not be all whitespace.")

    return violations
