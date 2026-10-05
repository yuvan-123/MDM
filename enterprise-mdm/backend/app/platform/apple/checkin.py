"""Apple MDM check-in / command-queue protocol helpers (Phase 9) — pure
plist parsing/construction, no DB access (that lives in
app/platform/apple/router.py, mirroring the Android webhook/router split).

## The two channels, and what this module builds
`app/platform/apple/provider.py`'s module docstring already describes
Apple MDM's two-channel design: an outbound APNs wake-up (channel 1,
implemented since Phase 6) and a device-initiated HTTPS connection back to
the server's own command-queue endpoint (channel 2, built here). Real
deployments commonly serve `checkin` and `connect`/`server` at
organization-specific URLs (the enrollment profile's `CheckInURL`/
`ServerURL` fields are per-deployment, often per-organization in a
multi-tenant server) — this codebase's endpoints are org-scoped by path
segment (`/api/v1/mdm/apple/{organization_id}/...`) for exactly that
reason, not an invented convention.

## Device resolution — the honest limitation
Both endpoints resolve a device via its UDID through the *existing*
`ProviderDeviceLinkRepository.get_device_id(organization_id, "apple", udid)`
— no new table or column. **Nothing in this codebase populates that link
for a real Apple device.** Real Apple MDM enrollment happens through
Apple Business Manager / Automated Device Enrollment (ADE) issuing a
signed enrollment profile that ties a UDID to a specific MDM server
identity — building that is explicitly out of this phase's scope (Phase 9
brief: do not claim real Apple Automated Device Enrollment). So this
endpoint is correct in shape and will 404 any UDID it has never seen
linked, honestly reflecting that the link can currently only be created
directly against the database, not through any enrollment flow this
codebase runs. Same category of documented gap as Android's "no real
enrollment ever populates provider_device_id" (Phase 6/7/8).

`provider_device_id` in this table means the device's UDID for Apple rows
— note this is a *different* meaning than what
`app/platform/apple/provider.py::_require_push_identity` (outbound
dispatch) reads from `ProviderDeviceRef.provider_device_id`, where it's
treated as the device's APNs push token. This phase does not reconcile
that tension (neither direction is live-verifiable in this environment
regardless of which identifier is stored) — documented, not silently
papered over. See docs/architecture/phase-9.md §4.

## Command-queue semantics
Apple's real protocol allows **at most one outstanding command per
device** — the device long-polls `connect`, the server returns the next
queued command (or an empty response meaning "nothing queued"), the
device attempts it and POSTs a Status response before it will ever be
offered another. This maps directly onto `DeviceCommand.status == SENT`:
there should only ever be one SENT command per device at a time for this
provider (a real deployment would need to enforce that at dispatch time —
this phase surfaces the existing single-`SENT`-per-device pattern rather
than adding new enforcement, since Phase 6/8's dispatch flow already only
ever produces one SENT row per issued command and nothing here changes
that).
"""

from __future__ import annotations

import plistlib
from typing import Any

# Apple's own MDM command RequestType strings — same mapping
# app/platform/apple/provider.py's dispatch side already uses, kept here
# independently rather than imported, since this module has no dependency
# on the provider/domain layer (pure protocol shape only).
COMMAND_TYPE_TO_REQUEST_TYPE = {
    "LOCK": "DeviceLock",
    "WIPE": "EraseDevice",
    "REFRESH_INFO": "DeviceInformation",
}

# Status values Apple's real protocol sends back for a delivered command.
STATUS_ACKNOWLEDGED = "Acknowledged"
STATUS_ERROR = "Error"
STATUS_COMMAND_FORMAT_ERROR = "CommandFormatError"
STATUS_NOT_NOW = "NotNow"


def parse_plist(body: bytes) -> dict[str, Any]:
    """Raises ValueError on anything malformed — untrusted external input,
    the caller turns this into a 400, never a 500."""
    try:
        data = plistlib.loads(body)
    except Exception as exc:
        raise ValueError(f"Could not parse plist body: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Plist body is not a dictionary.")
    return data


def require_udid(message: dict[str, Any]) -> str:
    udid = message.get("UDID")
    if not isinstance(udid, str) or not udid:
        raise ValueError("Message is missing a string 'UDID'.")
    return udid


def extract_push_token(message: dict[str, Any]) -> str | None:
    """A TokenUpdate message's `Token` field is raw APNs push-token bytes
    (plist `<data>`), decoded by `plistlib` into a Python `bytes` object —
    represented here as its hex string for storage, matching how every
    other provider identifier in this codebase is a plain string column."""
    token = message.get("Token")
    if isinstance(token, bytes):
        return token.hex()
    if isinstance(token, str) and token:
        return token
    return None


def build_command_plist(command_uuid: str, command_type: str, payload: dict[str, Any]) -> bytes:
    request_type = COMMAND_TYPE_TO_REQUEST_TYPE.get(command_type)
    if request_type is None:
        raise ValueError(f"No Apple RequestType mapping for command_type {command_type!r}.")
    fields: dict[str, Any] = {}
    if command_type == "LOCK" and payload.get("message"):
        fields["Message"] = payload["message"]
    command_dict: dict[str, Any] = {"RequestType": request_type, **fields}
    return plistlib.dumps({"CommandUUID": command_uuid, "Command": command_dict})


def empty_dequeue_plist() -> bytes:
    """An empty plist dictionary is this protocol's "nothing queued for
    you" signal — the device stops polling until its next scheduled
    check-in."""
    return plistlib.dumps({})


def map_status_to_event_type(status_value: str) -> str | None:
    """Returns None for NotNow — that means "ask me again later," not a
    terminal outcome, so it must produce no ProviderEvent / command
    transition at all, matching Phase 6's command state machine having no
    transition for it either."""
    if status_value == STATUS_ACKNOWLEDGED:
        return "COMMAND_COMPLETED"
    if status_value in (STATUS_ERROR, STATUS_COMMAND_FORMAT_ERROR):
        return "COMMAND_FAILED"
    return None
