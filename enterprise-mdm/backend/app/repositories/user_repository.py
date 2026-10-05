"""User repository — the ONLY layer that constructs SQLAlchemy queries for users.

Rules:
- Services call this; routes never call this directly.
- No business logic here; only query construction and result mapping.
- All methods that retrieve a user by something other than ID are
  explicitly typed to return User | None so callers must handle the
  not-found case explicitly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User, UserStatus


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Look up by normalized email."""
        normalized = User.normalize_email(email)
        result = await self._session.execute(
            select(User).where(User.email == normalized)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        first_name: str,
        last_name: str,
        status: UserStatus = UserStatus.ACTIVE,
        is_email_verified: bool = False,
    ) -> User:
        user = User(
            email=User.normalize_email(email),
            password_hash=password_hash,
            first_name=first_name,
            last_name=last_name,
            status=status,
            is_email_verified=is_email_verified,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def update(self, user: User, **fields: object) -> User:
        """Apply arbitrary field updates to *user* and flush."""
        for key, value in fields.items():
            setattr(user, key, value)
        await self._session.flush()
        return user

    async def update_last_login(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=datetime.now(tz=UTC), failed_login_attempts=0)
        )

    async def increment_failed_logins(self, user_id: uuid.UUID) -> int:
        """Increment failed_login_attempts and return the new count."""
        result = await self._session.execute(
            select(User.failed_login_attempts).where(User.id == user_id)
        )
        current = result.scalar_one_or_none() or 0
        new_count = current + 1
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(failed_login_attempts=new_count)
        )
        return new_count

    async def reset_failed_logins(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(failed_login_attempts=0, locked_until=None)
        )

    async def lock_until(self, user_id: uuid.UUID, until: datetime) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                status=UserStatus.LOCKED,
                locked_until=until,
            )
        )

    async def update_status(self, user_id: uuid.UUID, status: UserStatus) -> None:
        await self._session.execute(
            update(User).where(User.id == user_id).values(status=status)
        )

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash)
        )

    async def mark_email_verified(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(is_email_verified=True, status=UserStatus.ACTIVE)
        )

    async def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[User]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_users(self) -> int:
        from sqlalchemy import func

        result = await self._session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())
