# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in EnterpriseMDM, please report it
privately rather than opening a public issue. (Fill in a real contact
channel — e.g. a security@ mailbox or private advisory link — before this
project accepts external contributions or is deployed for real
organizations.)

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce
- Any relevant logs, requests, or proof-of-concept code

We will acknowledge reports and work with you on a coordinated disclosure
timeline.

## Security Posture Summary

See `docs/architecture/security-architecture.md` for full detail. Summary:

- Authentication: password hashing via Argon2id, JWT access tokens with
  short expiry, rotating refresh tokens, account lockout on repeated
  failures. (Phase 2)
- Authorization: permission-based RBAC, checked server-side on every
  request; never trusted from the frontend alone.
- Multi-tenancy: tenant isolation enforced redundantly at API, service,
  repository, and database (planned RLS) layers — see
  `docs/architecture/multi-tenancy.md`.
- Secrets: never committed; sourced from environment variables locally and
  a managed secret store in production.
- Auditability: security-sensitive operations produce append-only audit log
  entries.
- Data minimization: only device data mapped to a documented, supported
  Android Enterprise management purpose is ever collected. Hidden
  surveillance, message/call interception, microphone/camera capture,
  keylogging, credential harvesting, undisclosed location tracking, and
  arbitrary file extraction are explicitly out of scope and will not be
  implemented.

## Scope

This document currently describes a Phase 1 foundation with no
authentication or device-management functionality implemented yet. It will
be updated as each subsequent phase lands.
