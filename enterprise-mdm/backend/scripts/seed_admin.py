"""Seed script for Phase 2 authentication and RBAC.

Idempotently creates:
1. Built-in permissions (ALL_PERMISSIONS from app.rbac.permissions).
2. Built-in roles (BUILT_IN_ROLES from app.rbac.permissions).
3. Default SUPER_ADMIN administrator user account (if no user exists with SEED_ADMIN_EMAIL).

Usage:
    python scripts/seed_admin.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure backend root is on sys.path when invoked directly
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from app.auth.password import hash_password
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.database.session import AsyncSessionLocal
from app.rbac.permissions import SUPER_ADMIN
from app.rbac.service import RBACService
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.user_repository import UserRepository
from app.users.models import UserStatus

configure_logging()
logger = get_logger(__name__)


async def seed() -> None:
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        rbac_repo = RBACRepository(session)
        audit_repo = AuditRepository(session)
        rbac_service = RBACService(rbac_repo, user_repo, audit_repo)

        # 1. Seed roles and permissions
        logger.info("seeding_roles_and_permissions")
        await rbac_service.seed_roles_and_permissions()

        # 2. Check if default super admin exists
        existing_admin = await user_repo.get_by_email(settings.seed_admin_email)
        if existing_admin is not None:
            logger.info("seed_admin_already_exists", email=settings.seed_admin_email)
            await session.commit()
            return

        # 3. Create default super admin user
        logger.info("creating_seed_admin", email=settings.seed_admin_email)
        pwd_hash = hash_password(settings.seed_admin_password)
        admin_user = await user_repo.create(
            email=settings.seed_admin_email,
            password_hash=pwd_hash,
            first_name=settings.seed_admin_first_name,
            last_name=settings.seed_admin_last_name,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )

        # 4. Assign SUPER_ADMIN role
        super_admin_role = await rbac_repo.get_role_by_name(SUPER_ADMIN)
        assert super_admin_role is not None
        await rbac_repo.assign_role(
            user_id=admin_user.id,
            role_id=super_admin_role.id,
            granted_by=None,
            organization_id=None,
        )

        await session.commit()
        logger.info("seed_admin_created_successfully", email=settings.seed_admin_email)


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
