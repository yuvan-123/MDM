"""Async SQLAlchemy engine and session factory.

This is the only module permitted to construct the SQLAlchemy engine.
Repositories receive an `AsyncSession` via FastAPI dependency injection
(`get_db_session`) — they never construct their own engine/session.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    str(settings.database_url),
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped async session.

    Commits on a successful request, rolls back on any exception — this is
    the ONLY transaction boundary in the application; repositories only
    ever `flush()` (never `commit()`), matching every prior phase's
    convention of deferring durability to this single centralized point
    rather than scattering commits through the call stack. `get_db_session`
    is the sole `yield`-based FastAPI dependency in the whole app, so this
    is also the only place such a boundary could live: neither
    `RequestContextMiddleware` nor the exception handlers in
    `app/core/errors.py` ever get a reference to this session.

    FastAPI throws a route handler's exception into this generator at its
    `yield` point (via the dependency's AsyncExitStack) before it reaches
    `ExceptionMiddleware` — so `except Exception` below always sees the
    original, untransformed exception, rolls back, and re-raises it
    unchanged for `app/core/errors.py`'s handlers to process exactly as
    before. A commit-time failure is deliberately NOT caught here; it
    propagates to the existing catch-all `Exception` handler like any other
    unexpected error.

    `expire_on_commit=False` (set on AsyncSessionLocal above) is what makes
    adding this commit safe without touching the many repositories that
    `flush()` + `refresh()` an object mid-request specifically to avoid a
    `MissingGreenlet` error on a later synchronous `Pydantic.model_validate()`
    (see e.g. `DeviceRepository.update_for_organization`) — those
    already-refreshed attributes are not invalidated by the commit below.

    See docs/architecture/transaction-management.md for the full story,
    including why this was broken for every phase before this one: the
    test harness (`tests/conftest.py`) overrides this dependency entirely
    with one shared, never-closed session per test, so `flush()` alone was
    always enough for tests to pass — masking that a real per-request
    session, closed without a commit, silently rolls back everything.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """Used by the readiness endpoint. Returns True if a trivial query succeeds."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
