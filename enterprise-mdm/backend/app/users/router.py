"""User management API router.

Endpoints:
  GET    /api/v1/users                 (requires user.read)
  GET    /api/v1/users/{id}            (requires user.read)
  POST   /api/v1/users                 (requires user.create)
  PATCH  /api/v1/users/{id}           (requires user.update)
  PATCH  /api/v1/users/{id}/status    (requires user.update)
  POST   /api/v1/users/{id}/roles      (requires user.update)
  DELETE /api/v1/users/{id}/roles/{user_role_id}  (requires user.update)

All endpoints require explicit permission guards — never hardcoded role checks.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserResponse
from app.database.session import get_db_session
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, require_permission
from app.rbac.service import RBACService
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.users.schemas import (
    AssignRoleRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UpdateUserStatusRequest,
    UserDetailResponse,
    UserRoleResponse,
)
from app.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def _get_user_service(db: AsyncSession = Depends(get_db_session)) -> UserService:
    return UserService(
        user_repo=UserRepository(db),
        rbac_repo=RBACRepository(db),
        session_repo=SessionRepository(db),
        audit_repo=AuditRepository(db),
    )


def _get_rbac_service(db: AsyncSession = Depends(get_db_session)) -> RBACService:
    return RBACService(
        rbac_repo=RBACRepository(db),
        user_repo=UserRepository(db),
        audit_repo=AuditRepository(db),
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_READ))],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    service: UserService = Depends(_get_user_service),
) -> list[UserResponse]:
    """List platform users. Requires user.read permission."""
    users, _ = await service.list_users(offset=offset, limit=limit)
    return [UserResponse.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_READ))],
    user_service: UserService = Depends(_get_user_service),
    rbac_service: RBACService = Depends(_get_rbac_service),
) -> UserDetailResponse:
    """Get detailed user information including assigned roles. Requires user.read."""
    user = await user_service.get_by_id(user_id)
    user_roles = await rbac_service.get_user_roles(user_id)

    role_responses = [
        UserRoleResponse(
            id=ur.id,
            role_name=ur.role.name if ur.role else "UNKNOWN",
            granted_at=ur.granted_at,
            granted_by=ur.granted_by,
            organization_id=ur.organization_id,
        )
        for ur in user_roles
    ]

    detail = UserDetailResponse.model_validate(user)
    detail.roles = role_responses
    return detail


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_CREATE))],
    service: UserService = Depends(_get_user_service),
) -> UserResponse:
    """Create a new user. Requires user.create permission."""
    user = await service.create_user(
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        initial_role=body.initial_role,
        actor_user_id=current_user.user_id,
    )
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_UPDATE))],
    service: UserService = Depends(_get_user_service),
) -> UserResponse:
    """Update user names. Requires user.update permission."""
    user = await service.update_user(
        user_id=user_id,
        first_name=body.first_name,
        last_name=body.last_name,
        actor_user_id=current_user.user_id,
    )
    return UserResponse.model_validate(user)


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: uuid.UUID,
    body: UpdateUserStatusRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_UPDATE))],
    service: UserService = Depends(_get_user_service),
) -> UserResponse:
    """Update user status (ACTIVE, SUSPENDED, LOCKED, DISABLED).

    Disabling/suspending revokes active sessions immediately. Requires user.update.
    """
    user = await service.update_status(
        user_id=user_id,
        new_status=body.status,
        actor_user_id=current_user.user_id,
    )
    return UserResponse.model_validate(user)


@router.post("/{user_id}/roles", response_model=UserRoleResponse)
async def assign_role(
    user_id: uuid.UUID,
    body: AssignRoleRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_UPDATE))],
    rbac_service: RBACService = Depends(_get_rbac_service),
) -> UserRoleResponse:
    """Assign a role to a user. Requires user.update permission."""
    user_role = await rbac_service.assign_role(
        actor_user_id=current_user.user_id,
        target_user_id=user_id,
        role_name=body.role_name,
        organization_id=body.organization_id,
    )
    return UserRoleResponse(
        id=user_role.id,
        role_name=body.role_name,
        granted_at=user_role.granted_at,
        granted_by=user_role.granted_by,
        organization_id=user_role.organization_id,
    )


@router.delete("/{user_id}/roles/{user_role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    user_id: uuid.UUID,
    user_role_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.USER_UPDATE))],
    rbac_service: RBACService = Depends(_get_rbac_service),
) -> None:
    """Remove a role assignment from a user. Requires user.update permission."""
    await rbac_service.remove_role(
        actor_user_id=current_user.user_id,
        user_role_id=user_role_id,
    )
