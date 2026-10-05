"""User management service.

Coordinates user creation, updates, status changes, and listing.
Constructs no raw SQL/ORM queries — delegates strictly to UserRepository and RBACRepository.
"""

from __future__ import annotations

import uuid

from fastapi import status

from app.audit.models import AuditEventType
from app.auth.password import check_password_policy, hash_password
from app.core.errors import AppError
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.users.models import User, UserStatus


class UserService:
    def __init__(
        self,
        user_repo: UserRepository,
        rbac_repo: RBACRepository,
        session_repo: SessionRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._user_repo = user_repo
        self._rbac_repo = rbac_repo
        self._session_repo = session_repo
        self._audit_repo = audit_repo

    async def get_by_id(self, user_id: uuid.UUID) -> User:
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise AppError(
                code="NOT_FOUND",
                message="User not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return user

    async def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        users = await self._user_repo.list_users(offset=offset, limit=limit)
        total = await self._user_repo.count_users()
        return users, total

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        initial_role: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> User:
        # Check duplicate
        existing = await self._user_repo.get_by_email(email)
        if existing:
            raise AppError(
                code="EMAIL_EXISTS",
                message="A user with this email address already exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

        violations = check_password_policy(password)
        if violations:
            raise AppError(
                code="WEAK_PASSWORD",
                message=violations[0],
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        pwd_hash = hash_password(password)
        user = await self._user_repo.create(
            email=email,
            password_hash=pwd_hash,
            first_name=first_name,
            last_name=last_name,
            status=UserStatus.ACTIVE,
            is_email_verified=False,
        )

        if initial_role:
            role = await self._rbac_repo.get_role_by_name(initial_role)
            if role:
                await self._rbac_repo.assign_role(
                    user_id=user.id,
                    role_id=role.id,
                    granted_by=actor_user_id,
                )

        await self._audit_repo.log_event(
            event_type=AuditEventType.USER_CREATED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            metadata={"email": user.email, "initial_role": initial_role},
        )

        return user

    async def update_user(
        self,
        *,
        user_id: uuid.UUID,
        first_name: str | None = None,
        last_name: str | None = None,
        actor_user_id: uuid.UUID,
    ) -> User:
        user = await self.get_by_id(user_id)
        updates: dict[str, object] = {}
        if first_name is not None:
            updates["first_name"] = first_name
        if last_name is not None:
            updates["last_name"] = last_name

        if updates:
            user = await self._user_repo.update(user, **updates)
            await self._audit_repo.log_event(
                event_type=AuditEventType.USER_UPDATED,
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                metadata={"updated_fields": list(updates.keys())},
            )
        return user

    async def update_status(
        self,
        *,
        user_id: uuid.UUID,
        new_status: UserStatus,
        actor_user_id: uuid.UUID,
    ) -> User:
        user = await self.get_by_id(user_id)
        old_status = user.status
        if old_status == new_status:
            return user

        await self._user_repo.update_status(user_id, new_status)
        user.status = new_status

        # If disabling or locking, revoke all active sessions immediately.
        if new_status in (UserStatus.DISABLED, UserStatus.SUSPENDED, UserStatus.LOCKED):
            await self._session_repo.revoke_all_for_user(user_id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.USER_STATUS_CHANGED,
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            metadata={"old_status": old_status.value, "new_status": new_status.value},
        )

        return user
