"""Transaction-boundary fix (Phase 7) — proves the REAL `get_db_session`
commits on success and rolls back on failure, and that data survives
across two entirely separate session instances (i.e. across what would be
two separate HTTP requests in production).

Deliberately does NOT use the `client`/`db_session` fixtures from
conftest.py — those override `get_db_session` with one shared,
never-closed session per test, which is exactly the test-harness behavior
that masked this bug for six phases (see
docs/architecture/transaction-management.md). This file calls the real
`app.database.session.get_db_session` generator directly, monkeypatched
onto a file-backed (not `:memory:`) SQLite engine so persistence across
separate connections is the only variable under test — `:memory:` would
trivially "pass" for the wrong reason (a fresh `:memory:` connection has no
shared state at all, so the test would prove nothing).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import app.audit.models  # noqa: F401
import app.auth.models  # noqa: F401
import app.commands.models  # noqa: F401
import app.compliance.models  # noqa: F401
import app.device_groups.models  # noqa: F401
import app.devices.models  # noqa: F401
import app.enrollment.models  # noqa: F401
import app.organizations.models  # noqa: F401
import app.policies.models  # noqa: F401
import app.rbac.models  # noqa: F401
import app.users.models  # noqa: F401
from app.database import session as session_module
from app.database.base import Base
from app.organizations.models import Organization, OrganizationStatus
from app.repositories.organization_repository import OrganizationRepository


@pytest.fixture
async def file_backed_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncEngine, None]:
    """A real, file-backed SQLite engine (not `:memory:`) that outlives any
    single connection — the same property a real Postgres deployment has,
    which `:memory:` does not. Monkeypatches the module-level
    `AsyncSessionLocal` that `get_db_session` actually reads at call time,
    so the real, unmodified `get_db_session` function is exercised."""
    db_path = tmp_path / "transaction_persistence.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session_local = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", test_session_local)

    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_successful_request_commits_and_survives_a_new_session(
    file_backed_engine: AsyncEngine,
) -> None:
    """Simulates two separate HTTP requests: request 1 writes through the
    real get_db_session dependency and completes normally; request 2 opens
    a brand-new session (a new generator instance, exactly what FastAPI
    does per-request) and must see the write. Before the Phase 7 fix, this
    failed — the row existed only within request 1's own transaction and
    was rolled back the instant that generator closed."""
    org_id = uuid.uuid4()
    org_name = f"Persistence Test Org {org_id}"

    # --- "Request 1" ---
    gen = session_module.get_db_session()
    session = await anext(gen)
    org_repo = OrganizationRepository(session)
    await org_repo.create(name=org_name, slug=f"persistence-test-{org_id}")
    # Drain the generator to its natural end, exactly as FastAPI's
    # AsyncExitStack does when the route handler returns successfully —
    # this is what triggers the commit inside get_db_session.
    with pytest.raises(StopAsyncIteration):
        await anext(gen)

    # --- "Request 2" — an entirely new session/generator instance ---
    gen2 = session_module.get_db_session()
    session2 = await anext(gen2)
    result = await session2.execute(select(Organization).where(Organization.name == org_name))
    found = result.scalar_one_or_none()
    with pytest.raises(StopAsyncIteration):
        await anext(gen2)

    assert found is not None, (
        "Organization written in 'request 1' is not visible from a fresh "
        "session in 'request 2' — the transaction did not commit."
    )
    assert found.status == OrganizationStatus.ACTIVE


@pytest.mark.asyncio
async def test_failed_request_rolls_back_and_nothing_persists(
    file_backed_engine: AsyncEngine,
) -> None:
    """A request that raises must not leave a partial write behind —
    proves the `except Exception: rollback(); raise` branch."""
    org_id = uuid.uuid4()
    org_name = f"Rollback Test Org {org_id}"

    gen = session_module.get_db_session()
    session = await anext(gen)
    org_repo = OrganizationRepository(session)
    await org_repo.create(name=org_name, slug=f"rollback-test-{org_id}")

    class _SimulatedRouteFailureError(Exception):
        pass

    with pytest.raises(_SimulatedRouteFailureError):
        await gen.athrow(_SimulatedRouteFailureError("simulated failure after a write"))

    # Fresh session — the write above must be gone.
    gen2 = session_module.get_db_session()
    session2 = await anext(gen2)
    result = await session2.execute(select(Organization).where(Organization.name == org_name))
    found = result.scalar_one_or_none()
    with pytest.raises(StopAsyncIteration):
        await anext(gen2)

    assert found is None, "A write made before a request-ending exception must be rolled back."
