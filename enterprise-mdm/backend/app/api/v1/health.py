"""Liveness and readiness endpoints.

`/health` is a pure liveness check (process is up) with no dependency
checks — used by orchestrators to decide whether to restart the container.

`/ready` reports both database and Redis status, but only DATABASE gates
overall readiness (Phase 15). Redis in this codebase is exclusively a
fail-open rate-limit cache (`app/auth/rate_limit.py` — every helper there
already catches a Redis error and allows the request through rather than
blocking it) with the database as the authoritative source for anything
that must survive a Redis restart (account lockout state, etc.) — so a
Redis outage degrades rate-limiting, it does not make this instance unfit
to serve traffic. Taking the whole instance out of a load balancer's
rotation over a dependency this codebase already treats as best-effort
would be a self-inflicted outage. Postgres has no such fallback anywhere
in this codebase (every repository requires a session), so it remains a
hard gate. See docs/architecture/phase-15.md for the full reasoning and
docs/operations/disaster-recovery.md for the Redis-outage runbook.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.redis import check_redis_connection
from app.database.session import check_database_connection

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}


@router.get("/ready")
async def ready() -> JSONResponse:
    db_ok = await check_database_connection()
    redis_ok = await check_redis_connection()

    dependencies = {
        "database": "ok" if db_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
    # Database is the only hard gate — see this module's docstring.
    overall_ok = db_ok

    return JSONResponse(
        status_code=status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if overall_ok else "not_ready", "dependencies": dependencies},
    )
