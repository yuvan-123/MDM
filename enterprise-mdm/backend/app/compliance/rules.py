"""Compliance rule registry (STEP 9).

Each rule is a small, independent function: `(device, config) -> RuleResult
| None`. Returning `None` means the rule doesn't apply (the relevant
policy section wasn't configured) and is excluded from the result
entirely — it neither passes nor fails. `RULES` is the extension point:
adding a new check is adding a function here and appending it to the
list, never touching the evaluation loop in app/compliance/service.py.

Overall device status (STEP 9):
  - any rule FAILED               -> NON_COMPLIANT
  - no FAILED, at least one UNKNOWN -> UNKNOWN
  - no rules failed or unknown     -> COMPLIANT (including the case where
    zero rules applied at all — nothing to violate)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.devices.models import Device
from app.policies.schemas import PolicyConfiguration

RuleOutcome = Literal["PASS", "FAIL", "UNKNOWN"]


@dataclass(frozen=True)
class RuleResult:
    code: str
    outcome: RuleOutcome
    message: str
    # Phase 13: structured comparison values, for PolicyViolation display
    # (app/compliance/models.py) — optional, since not every outcome has a
    # meaningful single expected/actual pair (e.g. NO_POLICY_ASSIGNED).
    expected: str | None = None
    actual: str | None = None


RuleFn = Callable[[Device, PolicyConfiguration], RuleResult | None]


def _minimum_os_version(device: Device, config: PolicyConfiguration) -> RuleResult | None:
    required = config.device_security and config.device_security.minimum_os_version
    if not required:
        return None
    if not device.os_version:
        return RuleResult("MINIMUM_OS_VERSION", "UNKNOWN", "Device has not reported an OS version.")
    # Simple dotted-version comparison (e.g. "14.2" >= "14"). Not a full
    # semver parser — sufficient for OS major/minor version gating.
    try:
        actual = tuple(int(part) for part in device.os_version.split("."))
        required_tuple = tuple(int(part) for part in required.split("."))
    except ValueError:
        return RuleResult(
            "MINIMUM_OS_VERSION", "UNKNOWN", "OS version could not be compared."
        )
    if actual >= required_tuple:
        return RuleResult(
            "MINIMUM_OS_VERSION",
            "PASS",
            f"OS version {device.os_version} meets {required}.",
            expected=required,
            actual=device.os_version,
        )
    return RuleResult(
        "MINIMUM_OS_VERSION",
        "FAIL",
        f"OS version {device.os_version} is below required {required}.",
        expected=required,
        actual=device.os_version,
    )


def _require_encryption(device: Device, config: PolicyConfiguration) -> RuleResult | None:
    if not (config.device_security and config.device_security.require_encryption):
        return None
    if device.is_encrypted is None:
        return RuleResult("REQUIRE_ENCRYPTION", "UNKNOWN", "Encryption status not reported.")
    if device.is_encrypted:
        return RuleResult(
            "REQUIRE_ENCRYPTION", "PASS", "Device is encrypted.", expected="true", actual="true"
        )
    return RuleResult(
        "REQUIRE_ENCRYPTION",
        "FAIL",
        "Device is not encrypted.",
        expected="true",
        actual="false",
    )


def _require_screen_lock(device: Device, config: PolicyConfiguration) -> RuleResult | None:
    if not (config.device_security and config.device_security.require_screen_lock):
        return None
    if device.has_screen_lock is None:
        return RuleResult("REQUIRE_SCREEN_LOCK", "UNKNOWN", "Screen lock status not reported.")
    if device.has_screen_lock:
        return RuleResult(
            "REQUIRE_SCREEN_LOCK",
            "PASS",
            "Screen lock is enabled.",
            expected="true",
            actual="true",
        )
    return RuleResult(
        "REQUIRE_SCREEN_LOCK",
        "FAIL",
        "Screen lock is not enabled.",
        expected="true",
        actual="false",
    )


def _block_rooted_jailbroken(device: Device, config: PolicyConfiguration) -> RuleResult | None:
    if not (config.device_security and config.device_security.block_rooted_jailbroken):
        return None
    if device.is_rooted is None:
        return RuleResult(
            "BLOCK_ROOTED_JAILBROKEN", "UNKNOWN", "Root/jailbreak status not reported."
        )
    if device.is_rooted:
        return RuleResult(
            "BLOCK_ROOTED_JAILBROKEN",
            "FAIL",
            "Device is rooted/jailbroken.",
            expected="false",
            actual="true",
        )
    return RuleResult(
        "BLOCK_ROOTED_JAILBROKEN",
        "PASS",
        "Device is not rooted/jailbroken.",
        expected="false",
        actual="false",
    )


def _minimum_client_version(device: Device, config: PolicyConfiguration) -> RuleResult | None:
    required = config.compliance and config.compliance.minimum_client_version
    if not required:
        return None
    if not device.client_version:
        return RuleResult(
            "MINIMUM_CLIENT_VERSION", "UNKNOWN", "Device has not reported a client version."
        )
    try:
        actual = tuple(int(part) for part in device.client_version.split("."))
        required_tuple = tuple(int(part) for part in required.split("."))
    except ValueError:
        return RuleResult(
            "MINIMUM_CLIENT_VERSION", "UNKNOWN", "Client version could not be compared."
        )
    if actual >= required_tuple:
        return RuleResult(
            "MINIMUM_CLIENT_VERSION",
            "PASS",
            f"Client version {device.client_version} meets {required}.",
            expected=required,
            actual=device.client_version,
        )
    return RuleResult(
        "MINIMUM_CLIENT_VERSION",
        "FAIL",
        f"Client version {device.client_version} is below required {required}.",
        expected=required,
        actual=device.client_version,
    )


RULES: list[RuleFn] = [
    _minimum_os_version,
    _require_encryption,
    _require_screen_lock,
    _block_rooted_jailbroken,
    _minimum_client_version,
]
