# Authentication Architecture (Phase 2)

## 1. Overview

EnterpriseMDM implements production-quality administrator authentication designed around security, auditability, and defense in depth.

- **Primary authentication**: Email + password using Argon2id password hashing.
- **Transport**: HttpOnly cookies for browser sessions; `Authorization: Bearer` support for non-browser API clients.
- **Token model**: Short-lived JWT access tokens + server-side persisted refresh tokens with strict rotation and reuse detection.
- **Brute force defense**: Redis-backed per-IP rate limiting and database-authoritative account lockout.
- **Extensibility**: Provider abstraction designed so future Enterprise SSO (OIDC, SAML, Entra ID, Google Workspace) can be added without altering session/authorization contracts.

---

## 2. Password Security

### Hashing Algorithm
All user passwords are hashed using **Argon2id** (via `passlib`), configured with parameters exceeding OWASP minimums:
- `memory_cost`: 65,536 KiB (64 MB)
- `time_cost`: 2 iterations
- `parallelism`: 2 threads

Plaintext passwords, raw tokens, and MFA secrets are **never**:
- Persisted to storage
- Output in API responses
- Written to log files (enforced via `structlog` redaction denylist)

### Password Policy
Aligned with NIST SP 800-63B guidelines:
- Minimum length: 12 characters (configurable)
- Maximum length: 128 characters (DoS prevention)
- No arbitrary character composition rules (no forced special symbols or uppercase requirements)

---

## 3. Session Model & Token Lifecycle

### Refresh Token Storage
Raw refresh tokens are **never stored in the database**. When a session is created:
1. A cryptographically secure 48-byte URL-safe string is generated (`secrets.token_urlsafe(48)`).
2. The SHA-256 hex digest of the token is saved in `sessions.refresh_token_hash`.
3. The raw token is delivered once to the client in an `HttpOnly` cookie scoped to `/api/v1/auth/refresh`.

### Refresh Token Rotation & Reuse Detection
On every refresh request (`POST /api/v1/auth/refresh`):
1. The server computes the SHA-256 hash of the presented refresh token.
2. The database is queried for a matching session hash.
3. If the matching session is found **and `revoked_at` IS NOT NULL**, this indicates **Refresh Token Reuse** (a stolen token attack).
4. **Emergency action taken on reuse**:
   - Immediately revokes ALL active sessions for that user (`logout-all`).
   - Inserts a `TOKEN_REUSE_DETECTED` entry into `audit_logs`.
   - Inserts a `REFRESH_TOKEN_REUSE` entry into `security_events` with `CRITICAL` severity.
   - Rejects the request with HTTP 401.

---

## 4. Cookie Transport & CSRF Protection

For browser clients, tokens are managed via `Set-Cookie`:
- `access_token`: `HttpOnly`, `Path=/api/v1`, `SameSite=Lax` (or `Strict`), `Secure` in production. Expire: 15 mins.
- `refresh_token`: `HttpOnly`, `Path=/api/v1/auth/refresh`, `SameSite=Lax`, `Secure` in production. Expire: 7 days.
- `csrf_token`: `HttpOnly=false`, `Path=/`. JavaScript reads this value and includes it as an `X-CSRF-Token` header for state-changing HTTP methods (POST, PUT, PATCH, DELETE).

---

## 5. Multi-Factor Authentication (MFA) Status

**Status**: `PLANNED / NOT YET IMPLEMENTED`

The `User` ORM schema reserves:
- `mfa_enabled`: boolean flag
- `mfa_totp_secret`: text field (reserved for encrypted TOTP seed)

Future MFA methods will prioritize:
1. **WebAuthn / Passkeys** (FIDO2 YubiKeys, Touch ID, Face ID, Windows Hello)
2. **TOTP Authenticator Apps** (Google Authenticator, 1Password)
3. Enterprise IdP MFA via OIDC/SAML

*SMS-based MFA will not be implemented unless explicitly mandated by business requirements.*

---

## 6. Audit & Security Events

All authentication lifecycle events are logged to `audit_logs`:
- `LOGIN_SUCCESS`, `LOGIN_FAILED`
- `LOGOUT`, `LOGOUT_ALL`
- `TOKEN_REFRESH`, `TOKEN_REUSE_DETECTED`
- `PASSWORD_CHANGED`, `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET_COMPLETED`
- `SESSION_REVOKED`, `ACCOUNT_LOCKED`, `ACCOUNT_UNLOCKED`
