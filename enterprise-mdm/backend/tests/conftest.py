"""Test configuration and fixtures for Phase 2 + Phase 3.

Provides:
- In-memory database session fixture.
- Clean seed database (roles, permissions, default test admin).
- HTTP client fixture with custom session dependency override.
- Platform-role user fixtures (Super Admin, Help Desk, Auditor).
- Phase 3: organization + org-scoped-membership fixtures for multi-tenancy tests.
- `_login` helper to de-duplicate the login+CSRF boilerplate every test needs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.password import hash_password
from app.database.base import Base
from app.database.session import get_db_session
from app.main import app
from app.organizations.models import MembershipStatus, Organization
from app.rbac import permissions as p
from app.rbac.service import RBACService
from app.repositories.audit_repository import AuditRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.user_repository import UserRepository
from app.users.models import User, UserStatus


async def _login(client: AsyncClient, email: str, password: str) -> str:
    """Log in *client* and return the CSRF token for subsequent mutations.

    httpx's AsyncClient retains cookies across requests within a test
    automatically; only the CSRF token needs to be captured from the login
    response body and passed as the X-CSRF-Token header explicitly.
    """
    login_res = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login_res.status_code == 200, login_res.text
    csrf_token: str = login_res.json()["csrf_token"]
    return csrf_token

# Use SQLite in-memory engine for fast, isolated unit test execution.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@pytest.fixture(scope="session", autouse=True)
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create fresh database tables for each test and yield a clean session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        # Seed permissions & roles
        rbac_repo = RBACRepository(session)
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        rbac_service = RBACService(rbac_repo, user_repo, audit_repo)
        await rbac_service.seed_roles_and_permissions()
        await session.commit()

        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with DB session override."""
    async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def super_admin_user(db_session: AsyncSession) -> User:
    """Create a SUPER_ADMIN test user."""
    user_repo = UserRepository(db_session)
    rbac_repo = RBACRepository(db_session)

    pwd = hash_password("SuperSecret123!")
    user = await user_repo.create(
        email="superadmin@test.com",
        password_hash=pwd,
        first_name="Super",
        last_name="Admin",
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )

    role = await rbac_repo.get_role_by_name(p.SUPER_ADMIN)
    assert role is not None
    await rbac_repo.assign_role(
        user_id=user.id,
        role_id=role.id,
        granted_by=None,
    )
    await db_session.commit()
    return user


@pytest.fixture
async def helpdesk_user(db_session: AsyncSession) -> User:
    """Create a HELP_DESK test user."""
    user_repo = UserRepository(db_session)
    rbac_repo = RBACRepository(db_session)

    pwd = hash_password("HelpDeskPass123!")
    user = await user_repo.create(
        email="helpdesk@test.com",
        password_hash=pwd,
        first_name="Help",
        last_name="Desk",
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )

    role = await rbac_repo.get_role_by_name(p.HELP_DESK)
    assert role is not None
    await rbac_repo.assign_role(
        user_id=user.id,
        role_id=role.id,
        granted_by=None,
    )
    await db_session.commit()
    return user


@pytest.fixture
async def auditor_user(db_session: AsyncSession) -> User:
    """Create an AUDITOR test user."""
    user_repo = UserRepository(db_session)
    rbac_repo = RBACRepository(db_session)

    pwd = hash_password("AuditorPass123!")
    user = await user_repo.create(
        email="auditor@test.com",
        password_hash=pwd,
        first_name="Audit",
        last_name="User",
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )

    role = await rbac_repo.get_role_by_name(p.AUDITOR)
    assert role is not None
    await rbac_repo.assign_role(
        user_id=user.id,
        role_id=role.id,
        granted_by=None,
    )
    await db_session.commit()
    return user


# ---------------------------------------------------------------------------
# Phase 3: organizations + org-scoped memberships
# ---------------------------------------------------------------------------


async def _create_org(db_session: AsyncSession, name: str) -> Organization:
    org_repo = OrganizationRepository(db_session)
    org = await org_repo.create(name=name, slug=name.lower().replace(" ", "-"))
    await db_session.commit()
    return org


async def _create_org_member(
    db_session: AsyncSession,
    *,
    organization: Organization,
    email: str,
    password: str,
    role_name: str,
) -> User:
    """Create a platform user with an ACTIVE membership + org-scoped role
    in *organization*. Mirrors the pattern of super_admin_user/helpdesk_user
    but additionally wires up OrganizationMembership + a UserRole scoped to
    this org (not platform-scoped)."""
    user_repo = UserRepository(db_session)
    membership_repo = MembershipRepository(db_session)
    rbac_repo = RBACRepository(db_session)

    user = await user_repo.create(
        email=email,
        password_hash=hash_password(password),
        first_name="Org",
        last_name="Member",
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )
    await membership_repo.create(
        organization_id=organization.id, user_id=user.id, status=MembershipStatus.ACTIVE
    )
    role = await rbac_repo.get_role_by_name(role_name)
    assert role is not None
    await rbac_repo.assign_role(
        user_id=user.id,
        role_id=role.id,
        granted_by=None,
        organization_id=organization.id,
    )
    await db_session.commit()
    return user


@pytest.fixture
async def organization_a(db_session: AsyncSession) -> Organization:
    return await _create_org(db_session, "Organization A")


@pytest.fixture
async def organization_b(db_session: AsyncSession) -> Organization:
    return await _create_org(db_session, "Organization B")


@pytest.fixture
async def org_a_admin_user(db_session: AsyncSession, organization_a: Organization) -> User:
    """ORGANIZATION_ADMIN scoped to organization_a only."""
    return await _create_org_member(
        db_session,
        organization=organization_a,
        email="org-a-admin@test.com",
        password="OrgAAdminPass123!",
        role_name=p.ORGANIZATION_ADMIN,
    )


@pytest.fixture
async def org_a_member_user(db_session: AsyncSession, organization_a: Organization) -> User:
    """HELP_DESK scoped to organization_a only — a low-privilege member."""
    return await _create_org_member(
        db_session,
        organization=organization_a,
        email="org-a-member@test.com",
        password="OrgAMemberPass123!",
        role_name=p.HELP_DESK,
    )


@pytest.fixture
async def org_b_admin_user(db_session: AsyncSession, organization_b: Organization) -> User:
    """ORGANIZATION_ADMIN scoped to organization_b only."""
    return await _create_org_member(
        db_session,
        organization=organization_b,
        email="org-b-admin@test.com",
        password="OrgBAdminPass123!",
        role_name=p.ORGANIZATION_ADMIN,
    )


@pytest.fixture
async def org_b_member_user(db_session: AsyncSession, organization_b: Organization) -> User:
    """HELP_DESK scoped to organization_b only — a low-privilege member,
    symmetric to org_a_member_user (Phase 4 device tests)."""
    return await _create_org_member(
        db_session,
        organization=organization_b,
        email="org-b-member@test.com",
        password="OrgBMemberPass123!",
        role_name=p.HELP_DESK,
    )


@pytest.fixture
async def org_a_auditor_user(db_session: AsyncSession, organization_a: Organization) -> User:
    """AUDITOR scoped to organization_a only (Phase 4 device RBAC tests)."""
    return await _create_org_member(
        db_session,
        organization=organization_a,
        email="org-a-auditor@test.com",
        password="OrgAAuditorPass123!",
        role_name=p.AUDITOR,
    )


@pytest.fixture
async def org_a_security_admin_user(db_session: AsyncSession, organization_a: Organization) -> User:
    """SECURITY_ADMIN scoped to organization_a only (Phase 6 command RBAC tests)."""
    return await _create_org_member(
        db_session,
        organization=organization_a,
        email="org-a-security-admin@test.com",
        password="OrgASecurityAdminPass123!",
        role_name=p.SECURITY_ADMIN,
    )


@pytest.fixture
async def org_a_second_admin_user(
    db_session: AsyncSession, organization_a: Organization
) -> User:
    """A second ORGANIZATION_ADMIN in organization_a — an ACTIVE member
    distinct from org_a_admin_user, used as a device-assignment target."""
    return await _create_org_member(
        db_session,
        organization=organization_a,
        email="org-a-second-admin@test.com",
        password="OrgASecondAdminPass123!",
        role_name=p.ORGANIZATION_ADMIN,
    )


class _CapturingEmailService:
    """Test double for EmailService — captures invitation URLs (which
    contain the raw, single-use token) so tests can drive the full
    invite -> accept flow without a real mailbox. ConsoleEmailProvider only
    logs the URL; it never returns the raw token via any API response, by
    design (see docs/security/authentication.md's token-handling rules)."""

    def __init__(self) -> None:
        self.invitations: list[dict[str, str]] = []

    async def send_organization_invitation(
        self, to_email: str, organization_name: str, invite_url: str, expires_hours: int
    ) -> None:
        self.invitations.append({"to_email": to_email, "invite_url": invite_url})

    async def send_password_reset(
        self, to_email: str, reset_url: str, expires_minutes: int
    ) -> None:
        pass

    async def send_email_verification(
        self, to_email: str, verify_url: str, expires_hours: int
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# Phase 4: devices
# ---------------------------------------------------------------------------

from app.devices.enums import DevicePlatform, DeviceType, OwnershipType  # noqa: E402
from app.devices.models import Device  # noqa: E402
from app.repositories.device_repository import DeviceRepository  # noqa: E402


async def _create_device(
    db_session: AsyncSession, *, organization: Organization, display_name: str
) -> Device:
    device_repo = DeviceRepository(db_session)
    device = await device_repo.create_for_organization(
        organization.id,
        display_name=display_name,
        platform=DevicePlatform.ANDROID,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    await db_session.commit()
    return device


@pytest.fixture
async def device_in_org_a(db_session: AsyncSession, organization_a: Organization) -> Device:
    return await _create_device(db_session, organization=organization_a, display_name="Org A Phone")


@pytest.fixture
async def device_in_org_b(db_session: AsyncSession, organization_b: Organization) -> Device:
    return await _create_device(db_session, organization=organization_b, display_name="Org B Phone")


@pytest.fixture
def captured_emails(monkeypatch: pytest.MonkeyPatch) -> _CapturingEmailService:
    """Patches the email service used by MembershipService.invite_member so
    tests can extract the raw invitation token from the captured invite URL."""
    capturer = _CapturingEmailService()
    monkeypatch.setattr(
        "app.organizations.membership_service.get_email_service", lambda: capturer
    )
    return capturer


# ---------------------------------------------------------------------------
# Phase 8: command dispatch is async (a background job), not inline within
# create_command — see app/commands/service.py's module docstring. Tests
# that need a command actually dispatched (not just PENDING) call this
# helper against the shared db_session immediately after creating it,
# mirroring exactly what app/workers/loop.py's COMMAND_DISPATCH handler
# does for one claimed job, without needing a full worker cycle.
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402

from app.commands.models import DeviceCommand  # noqa: E402
from app.commands.service import CommandService  # noqa: E402
from app.jobs.service import JobService  # noqa: E402
from app.platform.registry import PlatformProviderRegistry  # noqa: E402
from app.repositories.background_job_repository import BackgroundJobRepository  # noqa: E402
from app.repositories.device_command_repository import DeviceCommandRepository  # noqa: E402
from app.repositories.provider_device_link_repository import (  # noqa: E402
    ProviderDeviceLinkRepository,
)


async def dispatch_pending_command(
    db_session: AsyncSession, organization_id: _uuid.UUID, command_id: str | _uuid.UUID
) -> DeviceCommand:
    service = CommandService(
        command_repo=DeviceCommandRepository(db_session),
        device_repo=DeviceRepository(db_session),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=AuditRepository(db_session),
        job_service=JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db_session),
    )
    resolved_id = command_id if isinstance(command_id, _uuid.UUID) else _uuid.UUID(command_id)
    command = await service.process_dispatch_job(organization_id, resolved_id)
    await db_session.commit()
    return command
