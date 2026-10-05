"""PlatformCapability — the capability model referenced by
docs/architecture/android-enterprise.md and used by app/commands/service.py
to reject an unsupported command BEFORE ever calling into a provider.

Not every platform (or even every device on the same platform, depending on
management mode) supports every remote action. A provider declares what it
can attempt at all via `PlatformManagementProvider.supported_capabilities`
(app/platform/base/provider.py) — this is a distinct, coarser question than
whether one specific dispatch attempt then succeeds or fails.
"""

from __future__ import annotations

import enum


class PlatformCapability(str, enum.Enum):
    LOCK = "LOCK"
    REBOOT = "REBOOT"
    WIPE = "WIPE"
    REFRESH_INFO = "REFRESH_INFO"
    POLICY_SYNC = "POLICY_SYNC"
    UNENROLL = "UNENROLL"
    # Phase 14: app deployment reuses the exact same command/capability-gate
    # shape as every other remote action — see app/commands/service.py's
    # _CAPABILITY_BY_TYPE and app/platform/base/provider.py's
    # install_application/remove_application.
    APPLICATION_INSTALL = "APPLICATION_INSTALL"
    APPLICATION_REMOVE = "APPLICATION_REMOVE"
