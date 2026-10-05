"""Baseline security response headers (Phase 15).

A small, fixed set of defense-in-depth headers browsers already respect
without any frontend change — none of these replace CSP/CSRF/auth, they
narrow what a browser will do with a response if something else already
went wrong (a reflected script, a framed login page, a MIME-sniffed
upload). Deliberately minimal: no CSP here — this API serves JSON to a
separate frontend origin, never HTML a browser would render directly, so
a content-security-policy tuned for that frontend belongs in *its* own
deployment (e.g. reverse-proxy/CDN config), not hardcoded into this
backend's every JSON response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

_STATIC_HEADERS = {
    # Never let a browser guess a response's content-type from its body —
    # relevant mainly for any endpoint that ever returns user-influenced
    # text/plain (error messages, etc.).
    "X-Content-Type-Options": "nosniff",
    # This API is never meant to be iframed by any origin.
    "X-Frame-Options": "DENY",
    # Send the referring origin only, and only over HTTPS->HTTPS — never
    # leak a full authenticated URL (which could carry no query secrets
    # here today, but this is cheap, standard defense-in-depth).
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # No document.domain, XSS-auditor, or CDN feature this backend uses.
    "X-Permitted-Cross-Domain-Policies": "none",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _STATIC_HEADERS.items():
            response.headers.setdefault(name, value)
        # HSTS only ever makes sense once cookies/traffic are already
        # HTTPS-only (Settings.cookie_secure) — sending it over plain HTTP
        # local dev would be actively misleading (the browser would still
        # honor it for a future real request to the same host). One year,
        # includeSubDomains — the standard baseline; no `preload` (that's
        # an irreversible, external-registry opt-in an operator must
        # choose deliberately, not something this codebase can decide on
        # their behalf).
        if get_settings().cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
