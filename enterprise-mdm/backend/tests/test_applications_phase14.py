"""Phase 14 — enterprise application management: catalog CRUD, platform
identifier validation, assignment, deployment via the mock provider,
required-app compliance integration + auto-deployment, provider capability
gating (Apple), RBAC, and tenant isolation. See app/applications/service.py
and docs/architecture/phase-14.md.

Does not re-test Phase 1-13 auth/RBAC/device/command/compliance mechanics
themselves (command dispatch/idempotency, compliance rule evaluation,
policy CRUD) — those are covered by tests/test_commands.py,
tests/test_compliance.py, etc., and unaffected by this phase except where
explicitly noted.

Drives the heartbeat -> job -> job worker chain via
`worker_loop.run_worker_once` against the shared test session, exactly
like tests/test_policy_violations_phase13.py already does.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.platform.android.provider import AndroidManagementProvider
from app.platform.apple.provider import AppleManagementProvider
from app.platform.base.capabilities import PlatformCapability
from app.platform.mock.provider import MockManagementProvider
from app.users.models import User
from app.workers import loop as worker_loop
from tests.conftest import _login
from tests.test_commands import _enroll_device
from tests.test_worker import _SharedSessionFactory


async def _run_worker_once(db_session: AsyncSession) -> bool:
    return await worker_loop.run_worker_once(_SharedSessionFactory(db_session))


async def _heartbeat(client: AsyncClient, device_id: str, device_secret: str) -> None:
    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert res.status_code == 200, res.text


async def _assign_trivial_active_policy(client: AsyncClient, csrf: str, device_id: str) -> None:
    """A device with NO policy at all always evaluates to UNKNOWN overall
    (ComplianceService.evaluate_device's pre-existing "NO_POLICY_ASSIGNED"
    RuleResult, unrelated to Phase 14) — `_overall_status` caps overall
    status at UNKNOWN whenever any result is UNKNOWN, so a required-app
    PASS alone could never surface as COMPLIANT without this. An empty
    configuration produces zero RuleResults from the existing RULES
    registry (every rule function returns None when its config section is
    absent), isolating the Phase 14 REQUIRED_APPLICATIONS_INSTALLED result
    as the only thing overall status can reflect."""
    create_res = await client.post(
        "/api/v1/policies",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Trivial Policy", "configuration": {}},
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
    assert assign_res.status_code == 200, assign_res.text


async def _create_android_app(
    client: AsyncClient, csrf: str, *, name: str = "Test App", package_name: str = "com.example.app"
) -> str:
    res = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json={"name": name, "platform": "ANDROID", "package_name": package_name},
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


@pytest.mark.asyncio
async def test_create_application_rejects_mismatched_identifier(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    missing_package = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Bad App", "platform": "ANDROID"},
    )
    assert missing_package.status_code == 422

    wrong_field = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Bad App",
            "platform": "ANDROID",
            "package_name": "com.example.app",
            "bundle_identifier": "com.example.App",
        },
    )
    assert wrong_field.status_code == 422

    ios_missing_bundle = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Bad iOS App", "platform": "IOS"},
    )
    assert ios_missing_bundle.status_code == 422


@pytest.mark.asyncio
async def test_application_catalog_crud(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    application_id = await _create_android_app(client, csrf)

    get_res = await client.get(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_res.status_code == 200
    assert get_res.json()["package_name"] == "com.example.app"

    list_res = await client.get("/api/v1/applications", headers={"X-CSRF-Token": csrf})
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1

    update_res = await client.patch(
        f"/api/v1/applications/{application_id}",
        headers={"X-CSRF-Token": csrf},
        json={"version": "2.0.0"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["version"] == "2.0.0"

    delete_res = await client.delete(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": csrf}
    )
    assert delete_res.status_code == 204

    get_after_delete = await client.get(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_after_delete.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_identifier_rejected(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await _create_android_app(client, csrf, package_name="com.example.dup")
    dup_res = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Duplicate", "platform": "ANDROID", "package_name": "com.example.dup"},
    )
    assert dup_res.status_code == 409


@pytest.mark.asyncio
async def test_assign_unassign_and_delete_blocked_while_assigned(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf, display_name="Assign Target")
    application_id = await _create_android_app(client, csrf)

    assign_res = await client.post(
        f"/api/v1/applications/{application_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": device_id, "requirement": "OPTIONAL"},
    )
    assert assign_res.status_code == 200, assign_res.text
    assert assign_res.json()["requirement"] == "OPTIONAL"

    assignments_res = await client.get(
        f"/api/v1/applications/{application_id}/assignments", headers={"X-CSRF-Token": csrf}
    )
    assert len(assignments_res.json()) == 1

    delete_blocked = await client.delete(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": csrf}
    )
    assert delete_blocked.status_code == 409

    unassign_res = await client.post(
        f"/api/v1/applications/{application_id}/unassign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": device_id},
    )
    assert unassign_res.status_code == 204

    delete_res = await client.delete(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": csrf}
    )
    assert delete_res.status_code == 204


@pytest.mark.asyncio
async def test_deploy_application_dispatches_install_command_via_mock_provider(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    """Covers the synchronous half of deploy -> command -> provider (brief
    §6): the ApplicationDeployment row transitions to INSTALLING and a real
    INSTALL_APPLICATION DeviceCommand is created and completed by the mock
    provider. The remaining half — reconciling that COMPLETED command back
    into the deployment's own INSTALLED status — is exercised together
    with the required-app compliance integration in
    test_required_application_missing_drives_compliance_and_auto_deploys
    below, since both paths share the exact same
    ApplicationService.reconcile_device_deployments code."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf, display_name="Deploy Target")
    application_id = await _create_android_app(client, csrf)

    deploy_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": csrf},
        json={"device_id": device_id},
    )
    assert deploy_res.status_code == 200, deploy_res.text
    deployment = deploy_res.json()["items"][0]
    assert deployment["status"] == "INSTALLING"
    assert deployment["last_command_id"] is not None

    command_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{deployment['last_command_id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert command_res.json()["command_type"] == "INSTALL_APPLICATION"
    assert command_res.json()["payload"] == {"package_name": "com.example.app"}
    assert command_res.json()["status"] == "PENDING"

    # COMMAND_DISPATCH job completes it via the mock provider.
    assert await _run_worker_once(db_session) is True
    command_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{deployment['last_command_id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert command_res.json()["status"] == "COMPLETED"

    # Re-deploying while already INSTALLING/INSTALLED is a no-op — see
    # ApplicationService.deploy_to_device's idempotency note.
    redeploy_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": csrf},
        json={"device_id": device_id},
    )
    assert redeploy_res.json()["items"][0]["id"] == deployment["id"]
    assert redeploy_res.json()["items"][0]["attempts"] == 1


@pytest.mark.asyncio
async def test_required_application_missing_drives_compliance_and_auto_deploys(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(
        client, csrf, display_name="Required App Target"
    )
    # A device with NO policy at all always evaluates to UNKNOWN overall —
    # see _assign_trivial_active_policy's own docstring — so a trivial
    # active policy is assigned first to isolate the required-app
    # compliance signal this test is actually about.
    await _assign_trivial_active_policy(client, csrf, device_id)
    application_id = await _create_android_app(client, csrf, name="Required App")

    assign_res = await client.post(
        f"/api/v1/applications/{application_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": device_id, "requirement": "REQUIRED"},
    )
    assert assign_res.status_code == 200, assign_res.text

    # First evaluation: required app is missing -> NON_COMPLIANT, and the
    # missing-app rule auto-triggers a deployment (see
    # ApplicationService.evaluate_required_applications).
    await _heartbeat(client, device_id, device_secret)
    assert await _run_worker_once(db_session) is True

    compliance_res = await client.get(
        f"/api/v1/compliance/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert compliance_res.status_code == 200
    assert compliance_res.json()["status"] == "NON_COMPLIANT"
    codes = {d["code"] for d in compliance_res.json()["details"]}
    assert "REQUIRED_APPLICATIONS_INSTALLED" in codes

    deployments_res = await client.get(
        f"/api/v1/applications/{application_id}/deployments", headers={"X-CSRF-Token": csrf}
    )
    assert deployments_res.json()["total"] == 1
    assert deployments_res.json()["items"][0]["status"] == "INSTALLING"
    assert deployments_res.json()["items"][0]["requirement"] == "REQUIRED"

    # Drain the COMMAND_DISPATCH job the auto-deploy enqueued.
    assert await _run_worker_once(db_session) is True

    # Next evaluation reconciles the now-COMPLETED command -> INSTALLED,
    # which makes the required-app rule pass -> COMPLIANT.
    await _heartbeat(client, device_id, device_secret)
    assert await _run_worker_once(db_session) is True

    compliance_res = await client.get(
        f"/api/v1/compliance/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert compliance_res.json()["status"] == "COMPLIANT"

    deployments_res = await client.get(
        f"/api/v1/applications/{application_id}/deployments", headers={"X-CSRF-Token": csrf}
    )
    assert deployments_res.json()["items"][0]["status"] == "INSTALLED"

    device_apps_res = await client.get(
        f"/api/v1/devices/{device_id}/applications", headers={"X-CSRF-Token": csrf}
    )
    assert device_apps_res.status_code == 200
    item = device_apps_res.json()["items"][0]
    assert item["requirement"] == "REQUIRED"
    assert item["deployment"]["status"] == "INSTALLED"


def test_mock_and_android_support_application_capabilities_apple_does_not() -> None:
    """Honesty gate (brief §7/§23): the mock provider (used by default in
    every other test in this file) and the real Android provider both
    declare APPLICATION_INSTALL/APPLICATION_REMOVE support; the real Apple
    provider deliberately does not (see that module's own docstring for
    why — no VPP-token integration exists in this codebase). Constructed
    directly, not through HTTP, since `supported_capabilities` is a pure
    property with no network I/O — no real Android/Apple credentials are
    exercised."""
    mock_provider = MockManagementProvider()
    assert PlatformCapability.APPLICATION_INSTALL in mock_provider.supported_capabilities
    assert PlatformCapability.APPLICATION_REMOVE in mock_provider.supported_capabilities

    android_settings = Settings(
        android_service_account_json='{"client_email": "x", "private_key": "y"}',
        android_enterprise_id="enterprises/fake",
    )
    android_provider = AndroidManagementProvider(android_settings)
    assert PlatformCapability.APPLICATION_INSTALL in android_provider.supported_capabilities
    assert PlatformCapability.APPLICATION_REMOVE in android_provider.supported_capabilities

    apple_settings = Settings(
        apple_apns_key_path="/fake/path.p8",
        apple_apns_key_id="FAKEKEYID",
        apple_apns_team_id="FAKETEAMID",
        apple_apns_topic="com.example.fake.mdm",
    )
    apple_provider = AppleManagementProvider(apple_settings)
    assert PlatformCapability.APPLICATION_INSTALL not in apple_provider.supported_capabilities
    assert PlatformCapability.APPLICATION_REMOVE not in apple_provider.supported_capabilities


@pytest.mark.asyncio
async def test_help_desk_cannot_create_or_deploy_applications(
    client: AsyncClient, org_a_admin_user: User, org_a_member_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    application_id = await _create_android_app(client, admin_csrf)

    member_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    create_res = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": member_csrf},
        json={"name": "Blocked App", "platform": "ANDROID", "package_name": "com.example.blocked"},
    )
    assert create_res.status_code == 403

    deploy_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": member_csrf},
        json={"device_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert deploy_res.status_code == 403

    # HELP_DESK still has application.read.
    read_res = await client.get(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": member_csrf}
    )
    assert read_res.status_code == 200


@pytest.mark.asyncio
async def test_org_b_cannot_see_or_deploy_org_a_application(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    application_id = await _create_android_app(client, admin_csrf)

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    get_res = await client.get(
        f"/api/v1/applications/{application_id}", headers={"X-CSRF-Token": b_csrf}
    )
    assert get_res.status_code == 404

    deploy_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": b_csrf},
        json={"device_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert deploy_res.status_code == 404


@pytest.mark.asyncio
async def test_deploy_request_requires_exactly_one_target(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    application_id = await _create_android_app(client, csrf)

    no_target_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert no_target_res.status_code == 422

    both_targets_res = await client.post(
        f"/api/v1/applications/{application_id}/deploy",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": "00000000-0000-0000-0000-000000000000",
            "organization_wide": True,
        },
    )
    assert both_targets_res.status_code == 422
