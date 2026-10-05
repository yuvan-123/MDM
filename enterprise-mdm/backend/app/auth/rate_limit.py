"""Redis-backed rate limiting and account lockout helpers.

Two independent mechanisms:
  1. Per-IP rate limit — prevents distributed brute-force from any IP.
     Key: rl:ip:<ip> → count of attempts in window.
  2. Per-account failed-login counter — persisted in the database (User model)
     not Redis, so it survives Redis restarts and is visible in the audit log.
     Redis is used ONLY as a cache of the lockout state to avoid a DB round-
     trip on every request. The database remains authoritative.

The rate_limit_login function checks the IP counter and raises AppError if
the limit is exceeded. The service layer handles account-level locking.
"""

from __future__ import annotations

from fastapi import status

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redis import get_redis_client


async def check_ip_rate_limit(ip: str) -> None:
    """Raise AppError(RATE_LIMITED) if this IP has exceeded the login limit.

    Uses a fixed-window counter in Redis. Fails open if Redis is unavailable
    (e.g. during standalone unit tests without live Redis infrastructure).
    """
    settings = get_settings()
    key = f"rl:login:ip:{ip}"

    try:
        client = get_redis_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count_str, ttl = await pipe.execute()
        count = int(count_str)

        if ttl == -1:
            await client.expire(key, settings.login_rate_limit_window_seconds)

        if count > settings.login_rate_limit_per_ip:
            raise AppError(
                code="RATE_LIMITED",
                message="Too many requests. Please try again later.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except AppError:
        raise
    except Exception:
        # Redis unavailable — log and fail open (allows offline unit tests & service resilience)
        return


async def check_password_reset_rate_limit(ip: str) -> None:
    """Rate limit password-reset requests per IP (10 per hour)."""
    key = f"rl:pwreset:ip:{ip}"

    try:
        client = get_redis_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count_str, ttl = await pipe.execute()
        count = int(count_str)

        if ttl == -1:
            await client.expire(key, 3600)  # 1-hour window

        if count > 10:
            raise AppError(
                code="RATE_LIMITED",
                message="Too many requests. Please try again later.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except AppError:
        raise
    except Exception:
        return


async def check_enrollment_rate_limit(ip: str) -> None:
    """Phase 15: rate limit unauthenticated device-registration attempts
    per IP. `POST /enrollment/register` is publicly reachable — the
    enrollment token itself is the authorization, there is no session or
    CSRF cookie to gate it — making it a token brute-force target in
    exactly the same shape `check_ip_rate_limit` above already protects
    `/auth/login` against. Reuses that same fixed-window Redis counter
    design (and the same `login_rate_limit_*` settings, rather than adding
    a dedicated enrollment knob for what is the identical "requests from
    one IP per window" concept) and the same fail-open-on-Redis-
    unavailable behavior — a Redis outage must never block a legitimate
    device's one-time registration attempt."""
    settings = get_settings()
    key = f"rl:enroll:ip:{ip}"

    try:
        client = get_redis_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count_str, ttl = await pipe.execute()
        count = int(count_str)

        if ttl == -1:
            await client.expire(key, settings.login_rate_limit_window_seconds)

        if count > settings.login_rate_limit_per_ip:
            raise AppError(
                code="RATE_LIMITED",
                message="Too many enrollment attempts. Please try again later.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except AppError:
        raise
    except Exception:
        # Redis unavailable — fail open, same as every other limiter here.
        return


async def reset_ip_rate_limit(ip: str) -> None:
    """Clear the IP rate limit counter after a successful login."""
    try:
        client = get_redis_client()
        await client.delete(f"rl:login:ip:{ip}")
    except Exception:
        pass
