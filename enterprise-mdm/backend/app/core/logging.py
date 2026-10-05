"""Structured logging configuration.

Every log line is JSON with a stable set of fields. A denylist filter
strips known-sensitive field names before a record is emitted, as defense
in depth against accidental secret logging.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from app.core.config import get_settings

_REDACTED_VALUE = "***REDACTED***"

# Field names that must never appear in logs, regardless of call site.
_SENSITIVE_KEYS = {
    "password",
    "hashed_password",
    "access_token",
    "refresh_token",
    "authorization",
    "jwt_secret",
    "api_key",
    "secret",
    "client_secret",
    "private_key",
}


def _redact_sensitive_fields(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED_VALUE
    return event_dict


def configure_logging() -> None:
    """Configure structlog + stdlib logging for the whole process."""
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive_fields,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name).bind(service="enterprise-mdm-backend")
