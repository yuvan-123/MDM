"""EnterpriseMDM backend entrypoint.

Wires auth, users, RBAC, organization/multi-tenancy, device inventory,
enrollment, device groups, policy, compliance, remote device commands
(Phase 6), background-job / provider-event admin visibility (Phase 8),
real Android/Apple provider webhooks (Phase 9), and provider-device-link
administration + job/event retry + unified provider status (Phase 10)
routes, CSRF protection middleware, structured logging, error envelope,
and CORS. See docs/architecture/background-jobs.md,
docs/architecture/provider-events.md, docs/architecture/phase-9.md, and
docs/architecture/phase-10.md.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health
from app.applications.router import device_applications_router
from app.applications.router import router as applications_router
from app.auth.middleware import CSRFMiddleware
from app.auth.router import router as auth_router
from app.commands.router import router as commands_router
from app.compliance.router import router as compliance_router
from app.core.config import get_settings, validate_production_config
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.device_groups.router import router as device_groups_router
from app.devices.router import router as devices_router
from app.enrollment.router import device_enrollment_router, enrollment_router
from app.jobs.router import router as jobs_router
from app.organizations.router import router as organizations_router
from app.platform.android.router import router as android_platform_router
from app.platform.apple.router import router as apple_platform_router
from app.platform.router import router as provider_status_router
from app.policies.router import router as policies_router
from app.provider_events.link_router import router as provider_device_links_router
from app.provider_events.router import router as provider_events_router
from app.users.router import router as users_router

settings = get_settings()
# Phase 15: fail fast, before this process ever binds a port or serves a
# single request, if APP_ENV=production but configuration is unsafe (an
# insecure default secret, HTTP-only cookies, a wildcard/localhost CORS
# origin — see app/core/config.py::validate_production_config). A no-op
# for every other app_env.
validate_production_config(settings)
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("application_startup", env=settings.app_env)
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.10.0",
        description="Multi-tenant Enterprise Mobile Device Management platform API.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    register_exception_handlers(app)

    # Unversioned infra endpoints
    app.include_router(health.router)
    # Exposed under /api/v1
    app.include_router(health.router, prefix=settings.api_v1_prefix)
    # Auth & RBAC routes
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(users_router, prefix=settings.api_v1_prefix)
    # Multi-tenancy routes (Phase 3)
    app.include_router(organizations_router, prefix=settings.api_v1_prefix)
    # Device inventory routes (Phase 4)
    app.include_router(devices_router, prefix=settings.api_v1_prefix)
    # Enrollment routes (Phase 5)
    app.include_router(enrollment_router, prefix=settings.api_v1_prefix)
    app.include_router(device_enrollment_router, prefix=settings.api_v1_prefix)
    # Device groups, policy, and compliance routes (Phase 5)
    app.include_router(device_groups_router, prefix=settings.api_v1_prefix)
    app.include_router(policies_router, prefix=settings.api_v1_prefix)
    app.include_router(compliance_router, prefix=settings.api_v1_prefix)
    # Remote device commands (Phase 6)
    app.include_router(commands_router, prefix=settings.api_v1_prefix)
    # Background job / provider-event admin visibility (Phase 8)
    app.include_router(jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(provider_events_router, prefix=settings.api_v1_prefix)
    # Real Android/Apple provider webhooks + health (Phase 9)
    app.include_router(android_platform_router, prefix=settings.api_v1_prefix)
    app.include_router(apple_platform_router, prefix=settings.api_v1_prefix)
    # Provider-device-link administration + unified provider status (Phase 10)
    app.include_router(provider_device_links_router, prefix=settings.api_v1_prefix)
    app.include_router(provider_status_router, prefix=settings.api_v1_prefix)
    # Application management (Phase 14)
    app.include_router(applications_router, prefix=settings.api_v1_prefix)
    app.include_router(device_applications_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
