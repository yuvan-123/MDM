# Transaction Management (Phase 7)

## 1. The bug

From Phase 1 through Phase 6, **nothing in `backend/app/` ever called
`session.commit()`** — every repository, across every phase, only called
`.flush()`. `app/database/session.py::get_db_session` (the sole
`yield`-based FastAPI dependency in the app) opened a session per request
and closed it at the end without committing:

```python
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

Closing an `AsyncSession` with a pending, uncommitted transaction rolls it
back. Confirmed empirically during Phase 6's manual UI verification:
logging in successfully three times against a real running instance (200,
valid-looking JWT each time), then querying the `sessions` table directly,
returned **0 rows** every time — the very next request using that "valid"
token got `401 SESSION_INVALID`.

This is severity-critical for any real (Postgres) deployment: logins
wouldn't persist sessions, and device creates/enrollments/policy
changes/command records — nothing would survive past the request that
created it.

## 2. Why six phases of tests never caught it

`tests/conftest.py`'s `client` fixture overrides `get_db_session` entirely,
yielding one shared, never-closed session for every request within a test:

```python
async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    yield db_session
```

All writes and reads within a test share one continuous transaction, so
`flush()` alone makes writes visible to later queries in the same test —
no commit is ever needed for tests to pass. This has been true since Phase
1; every subsequent phase inherited the same masking. No prior phase had
spun up the real `app.main.app` object and driven it through a real
browser across multiple real HTTP requests until Phase 6's manual
click-through — the first time this codebase actually exercised the real
per-request session lifecycle.

## 3. The fix

```python
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

This is safe as the *only* transaction boundary in the app because:

- `get_db_session` is the sole `yield`-based dependency — FastAPI throws a
  route handler's exception into this generator at its `yield` point (via
  the dependency's `AsyncExitStack`) *before* it reaches
  `ExceptionMiddleware`, so `except Exception` always sees the original,
  untransformed exception, rolls back, and re-raises it unchanged for
  `app/core/errors.py`'s handlers to process exactly as before.
- `AsyncSessionLocal` is configured with `expire_on_commit=False`, so the
  commit doesn't invalidate attributes that repositories already
  `flush()` + `refresh()` mid-request specifically to avoid a
  `MissingGreenlet` error on a later synchronous
  `Pydantic.model_validate()` call (e.g.
  `DeviceRepository.update_for_organization`).
- A commit-time failure is deliberately *not* caught — it propagates to
  the existing catch-all `Exception` handler like any other unexpected
  error, rather than being silently swallowed here.

Repositories still only ever `flush()`, never `commit()` — durability
stays deferred to this single centralized point, not scattered through the
call stack.

## 4. Verification

`tests/test_transaction_persistence.py` deliberately does **not** use the
`client`/`db_session` fixtures — those are exactly the test-harness
behavior that masked this bug for six phases. It instead calls the real
`app.database.session.get_db_session` generator directly, monkeypatched
onto a file-backed (not `:memory:`) SQLite engine, so persistence across
genuinely separate connections is the only variable under test:

1. **Positive case**: a write through one `get_db_session` generator
   instance, drained to completion (simulating a successful request), must
   be visible from a brand-new second generator instance (simulating a
   separate request).
2. **Negative case**: a write followed by `gen.athrow(...)` (simulating a
   route handler raising) must leave nothing behind in a fresh session.

Both were additionally verified by *temporarily reverting the fix* and
confirming test 1 fails with exactly the predicted `assert found is not
None` error, then restoring the fix and confirming both tests pass again —
stronger evidence than a green test suite alone, since a subtly wrong fix
could otherwise still pass a naively-written persistence test.

## 5. What's still open

Genuine multi-worker/concurrent-request behavior under real Postgres
remains unverified — Docker/Postgres has never been available in this
development environment (confirmed again at Phase 7 start). The fix is
correct by direct reading of FastAPI's dependency-injection
exception-propagation order and has been exercised against a file-backed
SQLite engine, but Postgres-specific transaction/locking behavior (e.g.
under `app/workers/loop.py`'s concurrent job claiming — see
`docs/architecture/background-jobs.md`) has not been proven against a live
database.
