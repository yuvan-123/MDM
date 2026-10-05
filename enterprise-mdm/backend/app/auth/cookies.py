"""Cookie helpers for auth token transport.

Decision: HttpOnly cookies are the safest transport for browser-based
sessions. See docs/security/authentication.md for full rationale.

Cookie names:
  access_token   — short-lived JWT; HttpOnly; Path=/api/v1
  refresh_token  — long-lived opaque token; HttpOnly; Path=/api/v1/auth/refresh
                   Scoped to the refresh endpoint so the browser only sends
                   it when explicitly refreshing, reducing exposure.
  csrf_token     — double-submit CSRF token; NOT HttpOnly so JS can read it
                   and attach it as a request header for mutation endpoints.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.core.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    """Set all auth cookies on the response.

    All tokens are passed as arguments — this function never generates them.
    """
    settings = get_settings()
    kwargs: dict[str, str | bool] = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain

    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        path="/api/v1",
        **kwargs,  # type: ignore[arg-type]
    )
    # Refresh token scoped to the refresh endpoint only.
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        path="/api/v1/auth/refresh",
        **kwargs,  # type: ignore[arg-type]
    )
    # CSRF cookie is NOT HttpOnly — the frontend JS reads it to send as header.
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def clear_auth_cookies(response: Response) -> None:
    """Remove all auth cookies (called on logout)."""
    response.delete_cookie(key=ACCESS_COOKIE, path="/api/v1")
    response.delete_cookie(key=REFRESH_COOKIE, path="/api/v1/auth/refresh")
    response.delete_cookie(key=CSRF_COOKIE, path="/")


def get_access_token_from_request(request: Request) -> str | None:
    """Extract the access token from either the cookie or Authorization header.

    Cookie takes precedence for browser clients; Authorization: Bearer is
    supported for programmatic/API clients (future mobile MDM agents).
    """
    cookie_val: str | None = request.cookies.get(ACCESS_COOKIE)
    if cookie_val:
        return cookie_val
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None


def get_refresh_token_from_request(request: Request) -> str | None:
    """Extract the refresh token from the cookie."""
    return request.cookies.get(REFRESH_COOKIE)


def get_csrf_token_from_request(request: Request) -> str | None:
    """Extract the CSRF token from the request header."""
    return request.headers.get(CSRF_HEADER)


def get_csrf_cookie_from_request(request: Request) -> str | None:
    """Extract the CSRF token from the cookie (for double-submit validation)."""
    return request.cookies.get(CSRF_COOKIE)
