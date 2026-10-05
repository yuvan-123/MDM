"""Phase 13 — policy violations and automated remediation: violation
lifecycle, idempotent creation, remediation trigger/attempt-limit/manual
retry, post-remediation reevaluation, policy-unassignment resolution,
NOTIFY mode, destructive-command rejection, RBAC, and tenant isolation.
See app/compliance/service.py, app/workers/loop.py, and
docs/architecture/phase-13.md.

Does not re-test Phase 5-12 compliance/policy/command mechanics (rule
evaluation itself, policy CRUD/versioning, command dispatch, idempotency
keys) — those are covered by tests/test_compliance.py, tests/test_policies.py,
tests/test_commands.py, etc., and unaffected by this phase except where
explicitly noted.

Drives the full heartbeat -> job -> job -> job chain via
`worker_loop.run_worker_once` against the shared test session, exactly
like tests/test_worker.py already does — one call per queued job, since
the worker processes at most one job per cycle.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus
from app.users.models import User
from app.workers import loop as worker_loop
from tests.conftest import _login
from tests.test_commands import _enroll_device
from tests.test_worker import _SharedSessionFactory


def _remediation_policy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Screen Lock Remediation Policy",
        "description": "Phase 13 test policy",
        "configuration": {
            "device_security": {"require_screen_lock": True},
            "remediation": {"mode": "COMMAND", "command_type": "LOCK", "max_attempts": 1},
        },
    }
    payload.update(overrides)
    return payload


async def _create_active_policy_assigned_to_device(
    client: AsyncClient, csrf: str, device_id: str, **payload_overrides: object
) -> str:
    create_res = await client.post(
        "/api/v1/policies",
        headers={"X-CSRF-Token": csrf},
        json=_remediation_policy_payload(**payload_overrides),
    )
    assert create_res.status_code == 201, create_res.text
    policy_id = create_res.json()["id"]

    activate_res = await client.patch(
        f"/api/v1/policies/{policy_id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "ACTIVE"},
    )
    assert activate_res.status_code == 200, activate_res.text

    assign_res = await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": device_id},
    )
    # app/policies/router.py's assign_policy sets no explicit status_code,
    # so FastAPI's default for a POST with a response_model is 200, not
    # 201 — unlike most other "create" endpoints in this codebase.
    assert assign_res.status_code == 200, assign_res.text
    return str(policy_id)


async def _run_worker_once(db_session: AsyncSession) -> bool:
    return await worker_loop.run_worker_once(_SharedSessionFactory(db_session))


async def _heartbeat(
    client: AsyncClient, device_id: str, device_secret: str, **fields: object
) -> None:
    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json=fields,
    )
    assert res.status_code == 200, res.text


async def _list_violations(
    client: AsyncClient, csrf: str, device_id: str
) -> list[dict[str, object]]:
    res = await client.get(
        f"/api/v1/compliance/violations?device_id={device_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    return list(res.json()["items"])


@pytest.mark.asyncio
async def test_violation_full_lifecycle_trigger_exhaust_retry_resolve(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(
        client, csrf, display_name="Remediation Target"
    )
    await _create_active_policy_assigned_to_device(client, csrf, device_id)

    # Assignment auto-enqueues the first evaluation. Device hasn't reported
    # screen-lock state yet -> UNKNOWN, not FAIL -> no violation.
    assert await _run_worker_once(db_session) is True
    assert await _list_violations(client, csrf, device_id) == []

    # Device reports screen lock disabled -> rule FAILs on next evaluation.
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True

    violations = await _list_violations(client, csrf, device_id)
    assert len(violations) == 1
    violation = violations[0]
    assert violation["rule_code"] == "REQUIRE_SCREEN_LOCK"
    assert violation["status"] == "REMEDIATION_PENDING"
    assert violation["remediation_attempts"] == 1
    violation_id = violation["id"]

    # The COMPLIANCE_REMEDIATION job creates the DeviceCommand.
    assert await _run_worker_once(db_session) is True
    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "REMEDIATING"
    assert violation["last_remediation_command_id"] is not None

    command_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{violation['last_remediation_command_id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert command_res.json()["command_type"] == "LOCK"
    assert command_res.json()["status"] == "PENDING"

    # COMMAND_DISPATCH job completes it via the mock provider.
    assert await _run_worker_once(db_session) is True
    command_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{violation['last_remediation_command_id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert command_res.json()["status"] == DeviceCommandStatus.COMPLETED.value

    # Device still reports the lock disabled (remediation "succeeded" as a
    # dispatch, but did not actually fix the device) -> next evaluation must
    # NOT assume compliance from the command alone (brief §13) — and with
    # max_attempts=1 already spent, this evaluation exhausts it.
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True
    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "REMEDIATION_FAILED"
    assert violation["remediation_attempts"] == 1

    # Manual retry resets attempts and re-triggers.
    retry_res = await client.post(
        f"/api/v1/compliance/violations/{violation_id}/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert retry_res.status_code == 200, retry_res.text
    assert retry_res.json()["status"] == "REMEDIATION_PENDING"
    assert retry_res.json()["remediation_attempts"] == 1

    # Drain the COMPLIANCE_REMEDIATION job the retry enqueued before doing
    # anything else — claim_next is FIFO by scheduled_at, and this job is
    # older than the heartbeat-triggered evaluation below would be, so a
    # single worker cycle after the heartbeat would otherwise process this
    # one instead.
    assert await _run_worker_once(db_session) is True
    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "REMEDIATING"

    # Now the device actually reports the lock enabled -> resolved,
    # regardless of the in-flight remediation attempt.
    await _heartbeat(client, device_id, device_secret, has_screen_lock=True)
    assert await _run_worker_once(db_session) is True
    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "RESOLVED"
    assert violation["resolution_reason"] == "RULE_PASSED"


@pytest.mark.asyncio
async def test_repeated_evaluation_does_not_duplicate_violation(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf, display_name="Dedup Target")
    await _create_active_policy_assigned_to_device(
        client, csrf, device_id, configuration={"device_security": {"require_screen_lock": True}}
    )
    assert await _run_worker_once(db_session) is True  # initial UNKNOWN eval

    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True
    # No remediation configured this time (bare device_security-only
    # config) -> stays OPEN, no job enqueued to drain.
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True

    violations = await _list_violations(client, csrf, device_id)
    assert len(violations) == 1
    assert violations[0]["status"] == "OPEN"


@pytest.mark.asyncio
async def test_policy_unassignment_resolves_violation(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf, display_name="Unassign Target")
    policy_id = await _create_active_policy_assigned_to_device(
        client, csrf, device_id, configuration={"device_security": {"require_screen_lock": True}}
    )
    assert await _run_worker_once(db_session) is True
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True
    assert (await _list_violations(client, csrf, device_id))[0]["status"] == "OPEN"

    unassign_res = await client.post(
        f"/api/v1/policies/{policy_id}/unassign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": device_id},
    )
    assert unassign_res.status_code == 204, unassign_res.text

    assert await _run_worker_once(db_session) is True
    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "RESOLVED"
    assert violation["resolution_reason"] == "POLICY_UNASSIGNED"


@pytest.mark.asyncio
async def test_notify_mode_creates_violation_without_command(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf, display_name="Notify Target")
    await _create_active_policy_assigned_to_device(
        client,
        csrf,
        device_id,
        configuration={
            "device_security": {"require_screen_lock": True},
            "remediation": {"mode": "NOTIFY"},
        },
    )
    assert await _run_worker_once(db_session) is True
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True

    violation = (await _list_violations(client, csrf, device_id))[0]
    assert violation["status"] == "OPEN"
    assert violation["remediation_attempts"] == 0

    commands_res = await client.get(
        f"/api/v1/devices/{device_id}/commands", headers={"X-CSRF-Token": csrf}
    )
    assert commands_res.json()["total"] == 0


@pytest.mark.asyncio
async def test_destructive_remediation_command_type_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/policies",
        headers={"X-CSRF-Token": csrf},
        json=_remediation_policy_payload(
            configuration={
                "device_security": {"require_screen_lock": True},
                "remediation": {"mode": "COMMAND", "command_type": "WIPE", "max_attempts": 1},
            }
        ),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_help_desk_cannot_manage_remediation(
    client: AsyncClient, org_a_admin_user: User, org_a_member_user: User, db_session: AsyncSession
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, admin_csrf, display_name="RBAC Target")
    await _create_active_policy_assigned_to_device(
        client,
        admin_csrf,
        device_id,
        configuration={"device_security": {"require_screen_lock": True}},
    )
    assert await _run_worker_once(db_session) is True
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True
    violation_id = (await _list_violations(client, admin_csrf, device_id))[0]["id"]

    member_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    resolve_res = await client.post(
        f"/api/v1/compliance/violations/{violation_id}/resolve",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resolve_res.status_code == 403


@pytest.mark.asyncio
async def test_org_b_cannot_see_org_a_violations(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    db_session: AsyncSession,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(
        client, admin_csrf, display_name="Tenant Target"
    )
    await _create_active_policy_assigned_to_device(
        client,
        admin_csrf,
        device_id,
        configuration={"device_security": {"require_screen_lock": True}},
    )
    assert await _run_worker_once(db_session) is True
    await _heartbeat(client, device_id, device_secret, has_screen_lock=False)
    assert await _run_worker_once(db_session) is True
    violation_id = (await _list_violations(client, admin_csrf, device_id))[0]["id"]

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    get_res = await client.get(
        f"/api/v1/compliance/violations/{violation_id}", headers={"X-CSRF-Token": b_csrf}
    )
    assert get_res.status_code == 404
