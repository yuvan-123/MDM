"""Redis client factory and connectivity check.

Redis is used for caching, rate limiting (Phase 2+), and as the Celery
broker/result backend (Phase 25 background jobs). Phase 1 only needs a
readiness check.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

settings = get_settings()

_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    return _redis_client


async def check_redis_connection() -> bool:
    try:
        client = get_redis_client()
        return bool(await client.ping())
    except Exception:
        return False
