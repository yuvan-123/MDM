"""Android Pub/Sub push webhook — pure parsing/verification helpers (Phase 9).

Real Google Cloud Pub/Sub push subscription target for Android Management
API enterprise notifications — the "channel" Phase 6/7 documented as
missing (docs/architecture/device-commands.md §7). Kept separate from
app/platform/android/router.py (the actual FastAPI endpoint + DB access)
so the parsing/verification logic is independently unit-testable without a
database.

## Authentication
A shared verification token (`Settings.android_pubsub_verification_token`)
appended to the push subscription's own endpoint URL as a query parameter
— Google's own documented mechanism for authenticating a push endpoint in
addition to (or instead of) OIDC bearer-token verification. Chosen over
OIDC here because verifying an OIDC token means fetching and caching
Google's public JWKS at runtime, which this development environment has no
way to exercise or prove correct without live network access to Google's
infrastructure; a shared token is simpler, genuinely secure when compared
with constant-time equality and kept secret, and is Google's own
documented fallback mechanism. See docs/architecture/phase-9.md for why
OIDC verification is left for a later phase rather than implemented
half-verified.

## Device resolution
Android Management API's `device` field on every notification is the full
resource name `enterprises/{enterpriseId}/devices/{deviceId}` — only the
trailing `{deviceId}` segment is what this codebase's
`ProviderDeviceLink.provider_device_id` stores, matching what
app/platform/android/client.py's dispatch side already uses as the bare
identifier in its own issueCommand URL.

## Notification-type mapping
Best-effort and NOT verified against real Android traffic — no live
enterprise exists in this environment. Documented, not glossed over:
- STATUS_REPORT / DEVICE_REPORT_UPDATE -> DEVICE_CHECK_IN (schedules
  compliance evaluation, mirroring the existing heartbeat trigger)
- COMMAND -> COMMAND_ACKNOWLEDGED (Android's own notification does not
  reliably distinguish "acknowledged" from "genuinely completed" without a
  follow-up `operations.get` call this codebase does not make — see
  app/platform/android/provider.py's own documented gap; mapped to the
  weaker, honest claim rather than guessing at COMPLETED)
- anything else is passed through as the raw `notificationType` string, so
  an unrecognized type is still recorded (and correctly IGNORED by the
  handler registry, not dropped or misclassified).
"""

from __future__ import annotations

import base64
import hmac
import json
from typing import Any

_NOTIFICATION_TYPE_MAP = {
    "STATUS_REPORT": "DEVICE_CHECK_IN",
    "DEVICE_REPORT_UPDATE": "DEVICE_CHECK_IN",
    "COMMAND": "COMMAND_ACKNOWLEDGED",
}


def verify_push_token(configured_token: str | None, supplied_token: str | None) -> bool:
    """Constant-time comparison — a naive `==` on a secret is a timing
    side channel. Fails closed: no configured token means never
    authenticated, never "accept anything.\""""
    if not configured_token or not supplied_token:
        return False
    return hmac.compare_digest(configured_token, supplied_token)


def decode_pubsub_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Raises ValueError on a malformed envelope — the caller turns that
    into a 400, not a 500, since this is untrusted external input."""
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise ValueError("Envelope is missing a 'message' object.")
    data = message.get("data")
    if not isinstance(data, str):
        raise ValueError("Envelope 'message' is missing a string 'data' field.")
    try:
        decoded_bytes = base64.b64decode(data)
        payload = json.loads(decoded_bytes)
    except Exception as exc:
        raise ValueError(f"Could not decode notification payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Decoded notification payload is not a JSON object.")
    return payload


def extract_message_id(envelope: dict[str, Any]) -> str | None:
    message = envelope.get("message")
    if isinstance(message, dict):
        message_id = message.get("messageId")
        if isinstance(message_id, str) and message_id:
            return message_id
    return None


def extract_provider_device_id(device_resource_name: str) -> str:
    """`enterprises/{enterpriseId}/devices/{deviceId}` -> `{deviceId}`."""
    return device_resource_name.rsplit("/", 1)[-1]


def map_notification_to_event_type(notification_type: str) -> str:
    return _NOTIFICATION_TYPE_MAP.get(notification_type, notification_type)
