# Contributing

## Development Methodology

This project is built incrementally, phase by phase (see README.md
Roadmap). Before implementing a new module:

1. Inspect the existing repository and understand current architecture.
2. Identify dependencies on/from other modules.
3. Update design documentation under `docs/` if the change affects it.
4. Implement the module (route -> service -> repository layering; see
   `docs/architecture/architecture.md`).
5. Write tests (unit, integration, and — for anything tenant-scoped —
   explicit cross-tenant isolation tests).
6. Run tests, lint, and type checks locally; fix issues.
7. Update documentation.
8. Commit a logical, reviewable milestone.

Do not implement a feature "later" for critical security properties
(authorization, tenant isolation, audit logging) — those must ship with the
feature, not as a follow-up.

## Code Standards

- Backend: typed Python (mypy strict), Ruff-clean, async I/O, no business
  logic in route handlers.
- Frontend: typed TypeScript (`tsc --noEmit` clean), no authorization
  decisions made client-side only.
- No hardcoded secrets, tenant IDs, or device IDs anywhere in source.
- Every destructive device action (wipe, retire) requires explicit
  confirmation in both API contract and UI — never a single implicit click.

## Commit Style

Use clear, scoped commit messages describing the module and phase, e.g.
`feat(auth): add JWT access/refresh token issuance (Phase 2)`.

## Pull Requests

CI (`.github/workflows/ci.yml`) runs backend lint/typecheck/tests and
frontend lint/typecheck/build on every PR. All checks must pass before
merge. Docker images are only built on `main`.
