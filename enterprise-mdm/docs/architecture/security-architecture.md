# Security Architecture

## 1. Principles

- Zero trust between layers: the frontend is never a trust boundary; every
  authorization decision is re-verified server-side.
- Defense in depth: authorization is checked at API, service, and repository
  layers (see `multi-tenancy.md` for the tenant-isolation instance of this).
- Least privilege: RBAC is permission-based, not role-name-based, so new
  roles can be composed from existing permissions without code changes.
- Secure by default: new endpoints require authentication and an explicit
  permission unless deliberately marked public (e.g. `/health`).
- Immutable audit trail: audit events are insert-only; no API exposes update
  or delete on `audit_logs`.

## 2. Authentication (Phase 2)

- Password-based login with Argon2id (via `passlib`) password hashing.
  Bcrypt is an acceptable fallback only if Argon2 is unavailable in the
  deployment environment; the config must record which is active.
- JWT access tokens (short-lived, e.g. 15 minutes) + rotating refresh tokens
  stored server-side (in `sessions`) so they can be revoked.
- Account lockout after repeated failed logins, enforced via Redis counters,
  scoped per-account and per-IP.
- MFA-ready: the `users` table and auth service reserve fields/hooks for a
  TOTP secret and MFA-required flag, even though TOTP enrollment UI is a
  later phase.
- All authentication failures produce an `audit_logs` entry
  (`USER_LOGIN_FAILED`) without leaking whether the username or password was
  wrong (generic error message).

## 3. Authorization

- Permission strings follow `resource.action` (e.g. `device.wipe`).
- Roles are stored data (`roles`, `permissions`, `role_permissions`,
  `user_roles`), not hardcoded enums in application code, so an operator can
  define custom roles without a deployment.
- A FastAPI dependency (`require_permission("device.wipe")`) is attached per
  route; it resolves the current user's effective permission set from their
  roles within the request's organization context.
- Destructive device operations (wipe, retire) require both the specific
  permission and a confirmation payload from the client — never a single
  implicit click.

## 4. Secrets Management

- No secret is ever committed. `.env` is git-ignored; `.env.example`
  documents required variable names with placeholder values only.
- Local development reads secrets from environment variables (via
  `pydantic-settings`).
- Production deployment is documented (in `DEPLOYMENT.md`, later phase) to
  use a managed secret store (cloud secret manager or Vault) rather than
  plaintext environment files on disk.
- JWT signing secrets, database credentials, and any future Android
  Enterprise service-account credentials are all sourced the same way —
  through `Settings`, never hardcoded.

## 5. Transport & Headers

- HTTPS is mandatory in production; local development may run HTTP behind
  the Docker network with TLS terminated at a reverse proxy in later phases.
- CORS is restricted to an explicit allow-list read from configuration
  (`CORS_ORIGINS`), never `*` outside local dev.
- Standard security headers (HSTS, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy) are applied at the reverse-proxy layer once Nginx is
  introduced (Phase 13), and at the FastAPI middleware layer as defense in
  depth.

## 6. Input Handling & Injection Defense

- All request/response bodies are validated through Pydantic schemas — no
  endpoint accepts a raw untyped dict.
- SQLAlchemy's parameterized query construction is the only way the
  repository layer talks to PostgreSQL; no string-formatted SQL is
  permitted anywhere in the codebase.
- Frontend output is rendered through React's default escaping; any HTML
  injection point (e.g. rich text in reports) will require an explicit
  sanitizer, documented at the point it's introduced.

## 7. Rate Limiting

- Redis-backed rate limiting is applied to: login, password reset,
  enrollment endpoints, admin write APIs, command creation, and webhook
  receivers, per the limits documented in `API.md` once those endpoints
  exist.

## 8. Error Handling

- Production error responses use the standardized envelope
  (`{"error": {"code", "message", "request_id"}}`) and never include stack
  traces, SQL text, or internal hostnames.
- Full exception detail is only written to structured logs, tagged with the
  same `request_id` returned to the client, so operators can correlate
  without exposing internals to callers.

## 9. Logging Discipline

- Structured JSON logs include timestamp, level, service, request ID, and
  (once auth exists) organization ID and acting user ID.
- Passwords, tokens, API keys, and any future Android Enterprise credentials
  are never logged, including in error paths — enforced by routing all
  logging through a helper that redacts a documented denylist of field
  names.

## 10. What Is Explicitly Out of Scope

Per the product mandate, this platform will never implement: hidden
monitoring, message/call interception, microphone or camera capture,
keylogging, credential harvesting, undisclosed location tracking, or
arbitrary file exfiltration. Any device data collected must map to a
documented, supported Android Enterprise management API and a legitimate
enterprise management purpose (see `android-enterprise.md`).
