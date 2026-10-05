# EnterpriseMDM — System Architecture

Status: Phase 5 complete (Device Enrollment, Lifecycle, and Policy Management). This document reflects the architecture actually
implemented so far. Update it every time a phase changes system shape.

## 1. Purpose

EnterpriseMDM is a multi-tenant Mobile Device Management platform for private
organizations. Phase 1 established the backend/frontend skeleton. Phase 2 implemented
administrator authentication (email+password, Argon2id, JWT, refresh rotation, sessions)
and permission-based Role-Based Access Control (RBAC). Phase 3 implements the tenancy
unit itself — organizations, memberships, invitations, and org-scoped RBAC (see
`multi-tenancy.md`) — so every domain phase from Phase 4 onward is tenant-aware from
its first migration. Phase 4 implemented device *inventory* (see
`device-management.md`) — not enrollment, not management. Phase 5 (see
`enrollment-and-policy.md`) implements real enrollment (token-based
registration, a per-device heartbeat credential) and a versioned,
assignable policy + compliance-evaluation engine on top of that inventory.
Real platform *management* — actually issuing a command to a physical
device — remains future work behind the still-unimplemented
`PlatformManagementProvider`.

## 2. High-Level Component View

```
                         Admin (browser)
                                |
                             HTTPS
                                |
                    +----------------------+
                    |  Web Admin Portal    |
                    |  Next.js 15 + TS     |
                    +----------+-----------+
                                |
                       REST (Cookies/CSRF)
                                |
                    +----------------------+
                    |    MDM Backend       |
                    |    FastAPI (async)   |
                    +----------+-----------+
                                |
                 +--------------+--------------+
                 |              |              |
                 v              v              v
           PostgreSQL 16     Redis 7        Workers
         (system of record) (cache/queue)  (Postgres-backed
                                             polling loop,
                                             Phase 7 — see
                                             background-jobs.md;
                                             not Celery, which
                                             remains unused)
```

Not yet wired (planned for later phases, interfaces reserved now):

```
                    +----------------------+
                    | Platform Adapter     |
                    | (base interface)     |
                    +----------+-----------+
                                |
                    +----------------------+
                    | AndroidManagement    |
                    | Provider              |
                    +----------------------+
```

## 3. Backend Module Boundaries

`backend/app/` is organized by domain module:
- `auth/`: Login, refresh rotation, logout, password reset, sessions, cookies, rate limiting
- `users/`: User model, user management CRUD service & API
- `rbac/`: Data-driven Roles, Permissions, UserRole assignments, `CurrentUser` & `require_permission` guards
- `organizations/`: Organizations, memberships, invitations, `OrganizationContext` & `require_organization_permission` guards (Phase 3)
- `devices/`: Device inventory model/schemas/service/router, org-scoped lifecycle & assignment (Phase 4); heartbeat + connectivity state (Phase 5)
- `enrollment/`: Enrollment tokens, device self-registration, per-device heartbeat credential, approve/reject/revoke workflow (Phase 5)
- `device_groups/`: Org-scoped device groups + membership (Phase 5)
- `policies/`: Versioned, assignable policy engine + precedence resolution (Phase 5)
- `compliance/`: Extensible rule registry + compliance evaluation engine (Phase 5)
- `audit/`: Insert-only `AuditLog` and `SecurityEvent` models and repositories
- `core/`: Config, logging (denylist redaction), error envelope, request context & CSRF middleware
- `database/`: Async engine & session lifecycle

Rules enforced from Phase 1 onward:
- Route handlers contain no business logic — they call a service.
- Services contain no raw SQL/ORM query construction — they call a repository.
- Repositories are the only layer that touches SQLAlchemy `Session`/`select()`.
- Authorization is permission-based (`resource.action`) evaluated server-side per request.

## 4. Data Flow (Phase 2)

1. Browser loads Next.js portal → unauthenticated users redirected to `/login`.
2. Admin submits credentials → `POST /api/v1/auth/login`.
3. Backend validates password via Argon2id, creates `Session` record (storing SHA-256 hash of refresh token), sets HttpOnly `access_token` and `refresh_token` cookies + non-HttpOnly `csrf_token` cookie.
4. Subsequent API calls carry cookies + `X-CSRF-Token` header.
5. FastAPI route dependencies (`get_current_user`, `require_permission`) validate JWT signature, DB session state, and user permission set before invoking business logic.

## 5. Why Phase Ordering Matters

Multi-tenancy (Phase 3, implemented) and RBAC (Phase 2) are implemented
before any device or policy feature specifically because every subsequent
table and query must be written tenant-aware and permission-checked from
the start. Retrofitting tenant isolation onto an existing schema is how
cross-tenant data leaks happen in production MDM systems; this project
avoids that by sequencing foundation → auth → tenancy → domain features.
Phase 4 (device inventory) and Phase 5 (enrollment/policy/compliance)
both followed the tenant-scoped repository pattern `multi-tenancy.md` §2
establishes, from their first migrations (`0004_phase4_devices.py`,
`0005_phase5_enrollment_policy.py`) — see `device-management.md` §7 and
`enrollment-and-policy.md` for how each new repository applies it.

## 6. Extensibility for iOS/iPadOS

The Android-first constraint is enforced by keeping all platform-specific
enrollment/command/policy-mapping logic behind `PlatformManagementProvider`.
Core domain tables (devices, enrollment tokens, policies, compliance
results) store a `platform` enum field where relevant but no
Android-specific columns — confirmed in Phase 4 and reconfirmed in Phase
5: nothing in `enrollment_tokens`, `device_enrollments`, `policies`,
`policy_versions`, or `compliance_results` is Android-specific; the policy
`configuration` JSON blob (`app/policies/schemas.py::PolicyConfiguration`)
is deliberately platform-neutral too. Android-specific data will live in a
separate `android_device_details`-style extension table introduced in
Phase 6+, not on any core Phase 4/5 row. This is what lets an
`AppleManagementProvider` be added later without a core schema rewrite.

## 7. Related Documents

- `security-architecture.md` — authn/authz, secrets, defense in depth
- `multi-tenancy.md` — tenant isolation strategy and enforcement points
- `device-management.md` — device inventory model, lifecycle, and the
  inventory/enrollment/management distinction (Phase 4)
- `enrollment-and-policy.md` — real enrollment, heartbeat, device groups,
  policy engine, and compliance evaluation (Phase 5)
- `android-enterprise.md` — platform abstraction and capability mapping
