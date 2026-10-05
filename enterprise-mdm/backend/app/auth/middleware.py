"""CSRF (Double-Submit Cookie) validation middleware.

Enforces CSRF protection for state-changing HTTP methods (POST, PUT, PATCH, DELETE)
when authentication cookies are present.

Flow:
1. Safe methods (GET, HEAD, OPTIONS) pass unconditionally.
2. Unauthenticated requests (no access cookie) pass unconditionally (auth endpoints
   like /auth/login validate credentials separately).
3. If an access cookie IS present, the request must include an `X-CSRF-Token` header
   whose value EXACTLY matches the `csrf_token` cookie value.
4. If missing or mismatched, returns 403 Forbidden with standard error envelope.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from app.core.errors import _envelope

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Auth endpoints that accept unauthenticated POST (login, reset, forgot-password)
EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    # Public, single-use-token-gated (same trust model as reset-password) —
    # must stay exempt even when the caller's browser happens to carry an
    # unrelated, still-valid session cookie (e.g. an org admin's own
    # session, present because they just sent the invite from the same
    # browser). The invitation token itself is the authorization.
    "/api/v1/organizations/invitations/accept",
    # Same trust model, Phase 5: the enrollment token is the authorization.
    # A device has no session cookie of its own, but an admin testing the
    # flow from their own browser would otherwise trip CSRF on their
    # unrelated, still-valid session cookie — same bug class fixed for
    # invitations/accept above.
    "/api/v1/enrollment/register",
    # Phase 9: real inbound provider webhooks — Google Pub/Sub and a real
    # Apple device never carry our session cookie (this branch only fires
    # at all when an access cookie IS present), but an admin testing the
    # endpoint from their own authenticated browser tab would otherwise
    # trip CSRF on that unrelated cookie. Authentication for these paths is
    # the shared verification token (Android) / org-scoped URL plus
    # ProviderDeviceLink resolution (Apple) — see
    # app/platform/android/webhook.py and app/platform/apple/checkin.py.
    "/api/v1/webhooks/android/pubsub",
    "/health",
    "/ready",
}
# Same trust model as EXEMPT_PATHS above, but the path contains a dynamic
# segment so it can't be listed as a literal string.
EXEMPT_PATH_PATTERNS = (
    # device heartbeats authenticate via X-Device-Id/X-Device-Secret
    # headers, never a session cookie. A device has no cookie of its own,
    # but an admin testing the flow from their own browser would otherwise
    # trip CSRF on their unrelated, still-valid session cookie riding along
    # on the request.
    re.compile(r"^/api/v1/devices/[^/]+/heartbeat$"),
    # Phase 9: Apple MDM check-in/command-queue endpoints — org-scoped by
    # path segment, called by a real Apple device over its own connection,
    # never carrying our session cookie.
    re.compile(r"^/api/v1/mdm/apple/[^/]+/(checkin|connect)$"),
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            request.method in SAFE_METHODS
            or path in EXEMPT_PATHS
            or any(pattern.match(path) for pattern in EXEMPT_PATH_PATTERNS)
        ):
            return await call_next(request)

        access_cookie = request.cookies.get(ACCESS_COOKIE)
        if not access_cookie:
            # Not a cookie-authenticated request (could be Bearer token or unauthenticated)
            return await call_next(request)

        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        csrf_header = request.headers.get(CSRF_HEADER)

        if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
            request_id = getattr(request.state, "request_id", None)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=_envelope(
                    "CSRF_FAILED",
                    "CSRF token validation failed.",
                    request_id,
                ),
            )

        return await call_next(request)
