from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.database.base import Base

# Import all model modules here so Base.metadata is fully populated for migrations.
import app.audit.models  # noqa: F401
import app.auth.models  # noqa: F401
import app.organizations.models  # noqa: F401
import app.rbac.models  # noqa: F401
import app.users.models  # noqa: F401
import app.devices.models  # noqa: F401  (Phase 4 — missing here until Phase 5's cleanup)
import app.enrollment.models  # noqa: F401
import app.device_groups.models  # noqa: F401
import app.policies.models  # noqa: F401
import app.compliance.models  # noqa: F401
import app.commands.models  # noqa: F401  (Phase 6 — was missing here until Phase 7's cleanup, see docs/architecture/background-jobs.md)
import app.jobs.models  # noqa: F401
import app.provider_events.models  # noqa: F401
import app.applications.models  # noqa: F401  (Phase 14 — was missing here until Phase 15's cleanup)

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", str(settings.database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _ensure_wide_version_column(connection: Connection) -> None:
    """Alembic's own `alembic_version.version_num` column defaults to
    VARCHAR(32) — this project's revision IDs are long, descriptive
    strings (e.g. "0008_phase8_command_dispatch_and_provider_links", 47
    chars), so the very first revision beyond 32 characters would fail
    with StringDataRightTruncationError before ever reaching the actual
    schema migrations. Never caught before this phase because the
    migration chain had never been run against a real Postgres database
    at all (see docs/architecture/phase-15.md) — SQLite's test harness
    uses Base.metadata.create_all, never Alembic. Creates the table with a
    wide column up front (a no-op if it already exists with a compatible
    width) rather than renaming any revision ID, which would be exactly
    the kind of historical-migration rewrite this project's own rules
    forbid. Widens an already-narrower existing column too, so this is
    also safe to run against a partially-migrated database left over from
    before this fix existed."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(255) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
    )
    connection.execute(
        text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)")
    )


def do_run_migrations(connection: Connection) -> None:
    # Committed explicitly and separately from the migrations themselves —
    # SQLAlchemy 2.0 connections are implicitly "in a transaction" from
    # first use, so executing this setup step without its own commit
    # leaves it (and everything that follows, since Alembic's own
    # `begin_transaction()` below would then nest inside this same
    # unmanaged transaction rather than starting a fresh one) rolled back
    # when the connection closes at the end of `run_migrations_online`,
    # having never been durably committed at all. Caught by actually
    # running this against real Postgres for the first time — every
    # "Running upgrade" log line printed and the process exited 0, yet
    # nothing persisted, until this commit was added.
    _ensure_wide_version_column(connection)
    connection.commit()
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
