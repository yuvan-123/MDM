"""Authentication service.

This is the single authoritative location for all authentication business
logic. Routes delegate to this service; this service delegates storage to
repositories. No SQLAlchemy queries are constructed here.

Refresh-token reuse detection:
  When a refresh token hash is presented but the matching session is already
  revoked, we treat this as a stolen-token reuse attack:
    1. Revoke ALL sessions for the user (force re-login everywhere).
    2. Log a TOKEN_REUSE_DETECTED audit event.
    3. Create a REFRESH_TOKEN_REUSE security event.
    4. Return an error — the caller must re-authenticate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request, status

from app.audit.models import AuditEventType, SecurityEventSeverity, SecurityEventType
from app.auth.email import get_email_service
from app.auth.password import check_password_policy, hash_password, verify_password
from app.auth.rate_limit import (
    check_ip_rate_limit,
    check_password_reset_rate_limit,
    reset_ip_rate_limit,
)
from app.auth.tokens import (
    create_access_token,
    generate_otp_token,
    generate_refresh_token,
    hash_otp_token,
    hash_refresh_token,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.repositories.audit_repository import AuditRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.users.models import User, UserStatus

logger = get_logger(__name__)


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For in trusted proxy setups."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class AuthService:
    """Orchestrates authentication: login, refresh, logout, password management."""

    def __init__(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        token_repo: TokenRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._user_repo = user_repo
        self._session_repo = session_repo
        self._token_repo = token_repo
        self._audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(
        self,
        *,
        email: str,
        password: str,
        request: Request,
    ) -> tuple[str, str, str, uuid.UUID]:
        """Authenticate a user and create a session.

        Returns (access_token, refresh_token, csrf_token, session_id).
        NEVER logs password or tokens.

        Raises AppError on any failure — all failures return the SAME
        generic error code to prevent user enumeration.
        """
        settings = get_settings()
        ip = _client_ip(request)
        user_agent = request.headers.get("User-Agent", "")

        # 1. IP-level rate limiting (independent of account)
        await check_ip_rate_limit(ip)

        # Generic error used for all auth failures to prevent user enumeration.
        _auth_error = AppError(
            code="INVALID_CREDENTIALS",
            message="Invalid email or password.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

        # 2. Look up user
        user = await self._user_repo.get_by_email(email)
        if user is None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                ip_address=ip,
                user_agent=user_agent,
                metadata={
                    "reason": "user_not_found",
                    "email_normalized": User.normalize_email(email),
                },
            )
            raise _auth_error

        # 3. Check account status before verifying password (avoids
        #    unnecessary hash computation for disabled accounts).
        await self._check_account_available(user, ip, user_agent)

        # 4. Verify password
        if not verify_password(password, user.password_hash):
            new_count = await self._user_repo.increment_failed_logins(user.id)
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                actor_user_id=user.id,
                ip_address=ip,
                user_agent=user_agent,
                metadata={"reason": "wrong_password", "attempt": new_count},
            )

            if new_count >= settings.max_login_attempts:
                locked_until = datetime.now(tz=UTC) + timedelta(
                    minutes=settings.lockout_duration_minutes
                )
                await self._user_repo.lock_until(user.id, locked_until)
                await self._audit_repo.log_event(
                    event_type=AuditEventType.ACCOUNT_LOCKED,
                    target_user_id=user.id,
                    ip_address=ip,
                    metadata={"reason": "too_many_failed_logins"},
                )
                await self._audit_repo.log_security_event(
                    event_type=SecurityEventType.ACCOUNT_LOCKED_BRUTE_FORCE,
                    user_id=user.id,
                    ip_address=ip,
                    severity=SecurityEventSeverity.HIGH,
                    details={"attempt_count": new_count},
                )

            raise _auth_error

        # 5. Password correct — reset IP rate limit counter.
        await reset_ip_rate_limit(ip)
        await self._user_repo.reset_failed_logins(user.id)
        await self._user_repo.update_last_login(user.id)

        # 6. Create session and tokens.
        raw_refresh, refresh_hash = generate_refresh_token()
        import secrets as _secrets
        csrf_token = _secrets.token_urlsafe(32)

        expires_at = datetime.now(tz=UTC) + timedelta(
            days=settings.refresh_token_expire_days
        )
        db_session = await self._session_repo.create(
            user_id=user.id,
            refresh_token_hash=refresh_hash,
            expires_at=expires_at,
            ip_address=ip,
            user_agent=user_agent,
        )

        access_token = create_access_token(user.id, db_session.id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.LOGIN_SUCCESS,
            actor_user_id=user.id,
            ip_address=ip,
            user_agent=user_agent,
            metadata={"session_id": str(db_session.id)},
        )

        logger.info("login_success", user_id=str(user.id), session_id=str(db_session.id))
        return access_token, raw_refresh, csrf_token, db_session.id

    async def _check_account_available(
        self, user: User, ip: str, user_agent: str
    ) -> None:
        """Raise AppError if the account cannot be used for login."""
        settings = get_settings()

        if user.status in (UserStatus.DISABLED, UserStatus.SUSPENDED):
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                actor_user_id=user.id,
                ip_address=ip,
                user_agent=user_agent,
                metadata={"reason": f"account_{user.status.value.lower()}"},
            )
            raise AppError(
                code="ACCOUNT_INACTIVE",
                message="Your account is not active. Please contact your administrator.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if user.status == UserStatus.LOCKED:
            locked_until = user.locked_until
            if locked_until and locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=UTC)

            if locked_until and locked_until > datetime.now(tz=UTC):
                await self._audit_repo.log_event(
                    event_type=AuditEventType.LOGIN_FAILED,
                    actor_user_id=user.id,
                    ip_address=ip,
                    user_agent=user_agent,
                    metadata={"reason": "account_locked"},
                )
                raise AppError(
                    code="ACCOUNT_LOCKED",
                    message="Your account has been temporarily locked. Please try again later.",
                    status_code=status.HTTP_423_LOCKED,
                )
            else:
                # Lock TTL has expired — auto-unlock.
                await self._user_repo.update_status(user.id, UserStatus.ACTIVE)
                await self._user_repo.reset_failed_logins(user.id)
                await self._audit_repo.log_event(
                    event_type=AuditEventType.ACCOUNT_UNLOCKED,
                    target_user_id=user.id,
                    ip_address=ip,
                    metadata={"reason": "lockout_ttl_expired"},
                )

        if settings.email_verification_required and not user.is_email_verified:
            raise AppError(
                code="EMAIL_NOT_VERIFIED",
                message="Please verify your email address before logging in.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(
        self,
        *,
        raw_refresh_token: str,
        request: Request,
    ) -> tuple[str, str, str]:
        """Rotate refresh token and issue new access + refresh token pair.

        Returns (new_access_token, new_refresh_token, new_csrf_token).

        IMPORTANT: if the presented token hash matches a REVOKED session,
        this is treated as a token-reuse attack (see module docstring).
        """
        settings = get_settings()
        ip = _client_ip(request)

        token_hash = hash_refresh_token(raw_refresh_token)
        db_session = await self._session_repo.get_by_refresh_token_hash(token_hash)

        if db_session is None:
            # Unknown token — could be a forged or already-rotated token.
            await self._audit_repo.log_event(
                event_type=AuditEventType.TOKEN_REFRESH,
                ip_address=ip,
                metadata={"result": "unknown_token"},
            )
            raise AppError(
                code="INVALID_TOKEN",
                message="Session is invalid or expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if db_session.revoked_at is not None:
            # Token reuse attack detected.
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(db_session.user_id),
                session_id=str(db_session.id),
                ip=ip,
            )
            await self._session_repo.revoke_all_for_user(db_session.user_id)
            await self._audit_repo.log_event(
                event_type=AuditEventType.TOKEN_REUSE_DETECTED,
                actor_user_id=db_session.user_id,
                ip_address=ip,
                metadata={"session_id": str(db_session.id)},
            )
            await self._audit_repo.log_security_event(
                event_type=SecurityEventType.REFRESH_TOKEN_REUSE,
                user_id=db_session.user_id,
                ip_address=ip,
                severity=SecurityEventSeverity.CRITICAL,
                details={"session_id": str(db_session.id)},
            )
            raise AppError(
                code="TOKEN_REUSE_DETECTED",
                message="Security alert: session invalidated. Please log in again.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if not db_session.is_active:
            raise AppError(
                code="SESSION_EXPIRED",
                message="Session is invalid or expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        # Verify user is still active.
        user = await self._user_repo.get_by_id(db_session.user_id)
        if user is None or user.status not in (UserStatus.ACTIVE, UserStatus.INVITED):
            await self._session_repo.revoke(db_session.id)
            raise AppError(
                code="ACCOUNT_INACTIVE",
                message="Your account is not active.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        # Rotate: new tokens, update session record.
        new_raw_refresh, new_refresh_hash = generate_refresh_token()
        import secrets as _secrets
        new_csrf = _secrets.token_urlsafe(32)
        new_expires_at = datetime.now(tz=UTC) + timedelta(
            days=settings.refresh_token_expire_days
        )

        await self._session_repo.rotate_refresh_token(
            db_session.id, new_refresh_hash, new_expires_at
        )
        new_access = create_access_token(user.id, db_session.id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.TOKEN_REFRESH,
            actor_user_id=user.id,
            ip_address=ip,
            metadata={"session_id": str(db_session.id)},
        )

        return new_access, new_raw_refresh, new_csrf

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    async def logout(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        request: Request,
    ) -> None:
        ip = _client_ip(request)
        await self._session_repo.revoke(session_id)
        await self._audit_repo.log_event(
            event_type=AuditEventType.LOGOUT,
            actor_user_id=user_id,
            ip_address=ip,
            metadata={"session_id": str(session_id)},
        )

    async def logout_all(self, *, user_id: uuid.UUID, request: Request) -> None:
        ip = _client_ip(request)
        await self._session_repo.revoke_all_for_user(user_id)
        await self._audit_repo.log_event(
            event_type=AuditEventType.LOGOUT_ALL,
            actor_user_id=user_id,
            ip_address=ip,
        )

    # ------------------------------------------------------------------
    # Password management
    # ------------------------------------------------------------------

    async def change_password(
        self,
        *,
        user_id: uuid.UUID,
        current_session_id: uuid.UUID,
        old_password: str,
        new_password: str,
        request: Request,
    ) -> None:
        ip = _client_ip(request)
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise AppError(
                code="NOT_FOUND",
                message="User not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not verify_password(old_password, user.password_hash):
            raise AppError(
                code="INVALID_CREDENTIALS",
                message="Current password is incorrect.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        violations = check_password_policy(new_password)
        if violations:
            raise AppError(
                code="WEAK_PASSWORD",
                message=violations[0],
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        new_hash = hash_password(new_password)
        await self._user_repo.update_password_hash(user_id, new_hash)

        # Revoke all OTHER sessions; current session remains valid.
        # (A stricter policy could revoke all and force re-login.)
        await self._session_repo.revoke_all_for_user(user_id)

        # Re-create the current session so the user stays logged in.
        settings = get_settings()
        raw_refresh, refresh_hash = generate_refresh_token()
        expires_at = datetime.now(tz=UTC) + timedelta(
            days=settings.refresh_token_expire_days
        )
        await self._session_repo.create(
            user_id=user_id,
            refresh_token_hash=refresh_hash,
            expires_at=expires_at,
            ip_address=ip,
            user_agent=request.headers.get("User-Agent", ""),
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.PASSWORD_CHANGED,
            actor_user_id=user_id,
            ip_address=ip,
        )

    async def request_password_reset(
        self,
        *,
        email: str,
        request: Request,
        base_url: str,
    ) -> None:
        """Initiate password reset flow.

        IMPORTANT: This method NEVER reveals whether the email exists.
        It always returns successfully to the caller.
        """
        ip = _client_ip(request)
        await check_password_reset_rate_limit(ip)

        settings = get_settings()
        user = await self._user_repo.get_by_email(email)
        if user is None or user.status == UserStatus.DISABLED:
            # Silent no-op — do not reveal email existence.
            return

        raw_token, token_hash = generate_otp_token()
        expires_at = datetime.now(tz=UTC) + timedelta(
            minutes=settings.password_reset_token_expire_minutes
        )
        await self._token_repo.create_password_reset_token(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        reset_url = f"{base_url}/reset-password?token={raw_token}"
        email_service = get_email_service()
        await email_service.send_password_reset(
            to_email=user.email,
            reset_url=reset_url,
            expires_minutes=settings.password_reset_token_expire_minutes,
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.PASSWORD_RESET_REQUESTED,
            actor_user_id=user.id,
            ip_address=ip,
        )

    async def reset_password(
        self,
        *,
        raw_token: str,
        new_password: str,
        request: Request,
    ) -> None:
        ip = _client_ip(request)
        token_hash = hash_otp_token(raw_token)
        token_record = await self._token_repo.get_valid_reset_token(token_hash)

        if token_record is None:
            raise AppError(
                code="INVALID_TOKEN",
                message="Reset token is invalid or has expired.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        violations = check_password_policy(new_password)
        if violations:
            raise AppError(
                code="WEAK_PASSWORD",
                message=violations[0],
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        new_hash = hash_password(new_password)
        await self._user_repo.update_password_hash(token_record.user_id, new_hash)
        await self._token_repo.mark_reset_token_used(token_record.id)
        await self._session_repo.revoke_all_for_user(token_record.user_id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.PASSWORD_RESET_COMPLETED,
            actor_user_id=token_record.user_id,
            ip_address=ip,
        )

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------

    async def verify_email(self, *, raw_token: str, request: Request) -> None:
        ip = _client_ip(request)
        token_hash = hash_otp_token(raw_token)
        token_record = await self._token_repo.get_valid_verify_token(token_hash)

        if token_record is None:
            raise AppError(
                code="INVALID_TOKEN",
                message="Verification token is invalid or has expired.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await self._user_repo.mark_email_verified(token_record.user_id)
        await self._token_repo.mark_verify_token_used(token_record.id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.EMAIL_VERIFIED,
            actor_user_id=token_record.user_id,
            ip_address=ip,
        )

    async def send_verification_email(
        self,
        *,
        user_id: uuid.UUID,
        base_url: str,
    ) -> None:
        settings = get_settings()
        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.is_email_verified:
            return

        raw_token, token_hash = generate_otp_token()
        from datetime import timedelta

        expires_at = datetime.now(tz=UTC) + timedelta(
            hours=settings.email_verify_token_expire_hours
        )
        await self._token_repo.create_email_verification_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        verify_url = f"{base_url}/verify-email?token={raw_token}"
        email_service = get_email_service()
        await email_service.send_email_verification(
            to_email=user.email,
            verify_url=verify_url,
            expires_hours=settings.email_verify_token_expire_hours,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.EMAIL_VERIFICATION_SENT,
            actor_user_id=user_id,
        )
