# EnterpriseMDM

Multi-tenant Mobile Device Management platform for private organizations,
built Android-first with an architecture that supports adding iOS/iPadOS
later. Working product name — designed to be renamed without an
architecture rewrite (see `docs/architecture/architecture.md`).

**Current status: Phase 3 — Multi-Tenancy & Organization Management.**
Authentication, RBAC, and organization/tenant isolation are implemented.
No device management or Android Enterprise integration exists yet. See
`docs/architecture/` for the target architecture and phased roadmap
context, `docs/security/` for the authentication/RBAC/multi-tenancy
security reviews, and `docs/architecture/multi-tenancy.md` for the tenant
isolation design.

## Repository Layout

```
backend/         FastAPI service (Python 3.12+, SQLAlchemy 2.x, Alembic)
frontend/        Next.js admin portal (TypeScript, Tailwind CSS)
android/         Android Enterprise device client (reserved, Phase 5+)
infrastructure/  Reverse proxy, monitoring config (reserved, later phases)
docs/            Architecture, security, API, deployment documentation
scripts/         Developer utility scripts
.github/         CI workflows
```

## Local Development

Prerequisites: Docker and Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

This starts PostgreSQL, Redis, the backend API, and the frontend.

- Backend: http://localhost:8000 (docs at `/docs`, health at `/health`,
  readiness at `/ready`)
- Frontend: http://localhost:3000

### Running the backend without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install ".[dev]"
uvicorn app.main:app --reload
```

Backend test/lint/typecheck commands:

```bash
cd backend
pytest --cov=app --cov-report=term-missing
ruff check .
mypy .
```

### Running the frontend without Docker

```bash
cd frontend
npm install
npm run dev
```

Frontend lint/typecheck/build commands:

```bash
cd frontend
npm run lint
npm run typecheck
npm run build
```

## Documentation

- `docs/architecture/architecture.md` — system architecture
- `docs/architecture/security-architecture.md` — authn/authz, secrets
- `docs/architecture/multi-tenancy.md` — tenant isolation strategy
- `docs/architecture/android-enterprise.md` — platform abstraction and the
  research process required before any real Android Enterprise integration
  is implemented
- `SECURITY.md` — vulnerability reporting and security posture summary
- `CONTRIBUTING.md` — development workflow

## Roadmap

Phase 1 (foundation), Phase 2 (authentication & RBAC), and Phase 3
(multi-tenancy & organization management) are complete. Next: device
inventory with mock data (Phase 4), then verified Android Enterprise
integration (Phase 5) and onward through policies, applications,
compliance, commands, reporting, security hardening, and production
readiness. Each phase is implemented, tested, and documented before the
next begins — see the product specification for the full phase list.

## License

See `LICENSE`.
