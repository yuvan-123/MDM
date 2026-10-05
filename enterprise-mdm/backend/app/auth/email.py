"""Email service abstraction.

Architecture: The EmailService protocol defines the interface. Concrete
providers are selected via the EMAIL_PROVIDER setting:

  "console" — logs the email body to stdout via structlog. Used in local
              development so the reset link is visible without a real inbox.
  "null"    — discards all emails silently. Used in tests.
  "smtp"    — NOT YET IMPLEMENTED. Will be added in a future phase.

NEVER log token values from this module. The console provider logs the
reset URL which contains the token — that is intentional for dev only.
In production, EMAIL_PROVIDER must be "smtp" (or similar) so tokens
appear only in the email, not in logs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailService(ABC):
    """Abstract email service. Implement one concrete class per provider."""

    @abstractmethod
    async def send_password_reset(
        self,
        to_email: str,
        reset_url: str,
        expires_minutes: int,
    ) -> None:
        """Send a password reset email containing *reset_url*."""
        ...

    @abstractmethod
    async def send_email_verification(
        self,
        to_email: str,
        verify_url: str,
        expires_hours: int,
    ) -> None:
        """Send an email verification link."""
        ...

    @abstractmethod
    async def send_organization_invitation(
        self,
        to_email: str,
        organization_name: str,
        invite_url: str,
        expires_hours: int,
    ) -> None:
        """Send an organization invitation containing *invite_url*."""
        ...


class ConsoleEmailProvider(EmailService):
    """Development-only provider that logs emails to stdout.

    WARNING: This logs the reset/verify URL which contains a token.
    Only use EMAIL_PROVIDER=console in local development.
    """

    async def send_password_reset(
        self,
        to_email: str,
        reset_url: str,
        expires_minutes: int,
    ) -> None:
        logger.info(
            "dev_email_password_reset",
            to=to_email,
            reset_url=reset_url,
            expires_minutes=expires_minutes,
            note="[DEV] This URL would be emailed in production.",
        )

    async def send_email_verification(
        self,
        to_email: str,
        verify_url: str,
        expires_hours: int,
    ) -> None:
        logger.info(
            "dev_email_verification",
            to=to_email,
            verify_url=verify_url,
            expires_hours=expires_hours,
            note="[DEV] This URL would be emailed in production.",
        )

    async def send_organization_invitation(
        self,
        to_email: str,
        organization_name: str,
        invite_url: str,
        expires_hours: int,
    ) -> None:
        logger.info(
            "dev_email_organization_invitation",
            to=to_email,
            organization_name=organization_name,
            invite_url=invite_url,
            expires_hours=expires_hours,
            note="[DEV] This URL would be emailed in production.",
        )


class NullEmailProvider(EmailService):
    """Test provider that silently discards all emails."""

    async def send_password_reset(
        self,
        to_email: str,
        reset_url: str,
        expires_minutes: int,
    ) -> None:
        pass

    async def send_email_verification(
        self,
        to_email: str,
        verify_url: str,
        expires_hours: int,
    ) -> None:
        pass

    async def send_organization_invitation(
        self,
        to_email: str,
        organization_name: str,
        invite_url: str,
        expires_hours: int,
    ) -> None:
        pass


def get_email_service() -> EmailService:
    """Factory: return the configured email service provider."""
    settings = get_settings()
    if settings.email_provider == "null":
        return NullEmailProvider()
    if settings.email_provider == "smtp":
        raise NotImplementedError(
            "SMTP email provider is not yet implemented. "
            "Set EMAIL_PROVIDER=console for local development."
        )
    # Default: console
    return ConsoleEmailProvider()
