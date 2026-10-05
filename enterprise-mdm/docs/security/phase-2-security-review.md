# Phase 2 — Security Review

## 1. Scope
Phase 2 Authentication and Role-Based Access Control (RBAC) implementation review.

---

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Password Storage** | Argon2id password hashing via `passlib` (64MB memory, 2 iterations). Plaintext passwords never stored or logged. | `tests/test_security.py` | PASS |
| **Credential Harvesting / Leakage** | `password_hash`, `refresh_token_hash`, reset tokens excluded from all response schemas. Structlog denylist strips sensitive keys. | `tests/test_security.py` | PASS |
| **User Enumeration** | Login & password reset endpoints return generic messages (`INVALID_CREDENTIALS`, HTTP 204 no-op). | `tests/test_auth.py` | PASS |
| **Brute Force & Lockout** | Redis fixed-window per-IP rate limiting + DB account lockout after 5 failed attempts (15-min lockout). | `tests/test_auth.py` | PASS |
| **Token Theft & Replay** | HttpOnly, SameSite cookies. Refresh token rotation with stolen-token reuse detection (forces `logout-all` on reuse). | `tests/test_auth.py` | PASS |
| **CSRF** | Double-submit CSRF cookie pattern (`X-CSRF-Token` header) enforced by `CSRFMiddleware` for non-GET/HEAD methods. | `app/auth/middleware.py` | PASS |
| **Privilege Escalation** | Permission-based route guards (`require_permission`). Client request body JSON role properties ignored. | `tests/test_security.py` | PASS |
| **Session Revocation** | Account disable/suspend or password change revokes active sessions immediately. | `tests/test_security.py` | PASS |
| **Audit Compliance** | All auth lifecycle events recorded in insert-only `audit_logs` and high-severity security anomalies in `security_events`. | `app/audit/models.py` | PASS |

---

## 3. Discovered & Resolved Issues

1. **Stale Permission Window**: Initial design considered encoding permissions directly into JWT claims. **Resolution**: Switched to server-side permission resolution from the database via `CurrentUser` dependency on every request, eliminating stale permission risk on role changes.
2. **Refresh Token Scoping**: Refresh cookies were initially set for path `/`. **Resolution**: Restricted `refresh_token` cookie path strictly to `/api/v1/auth/refresh` so the browser only presents it during token rotation calls.

---

## 4. Known Limitations & Phase 3 Roadmap
- **SSO / Enterprise IdP**: SAML 2.0 / OIDC integrations are designed for and deferred to Phase 12.
- **MFA Enforcement**: TOTP / WebAuthn schema placeholders exist; enrollment UI deferred.
- **Organization Scoping**: UserRole assignments currently operate at platform scope (`organization_id = NULL`). Phase 3 will introduce organization FK scoping.
