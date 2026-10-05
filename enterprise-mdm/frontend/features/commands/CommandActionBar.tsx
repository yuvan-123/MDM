"use client";

import { useState } from "react";
import { Lock, RotateCw, RefreshCw, ShieldAlert, Trash2, Unlink, Loader2 } from "lucide-react";
import {
  useIssueLockCommand,
  useIssueRebootCommand,
  useIssueRefreshInfoCommand,
  useIssuePolicySyncCommand,
  useIssueWipeCommand,
  useIssueUnenrollCommand,
  useDeviceCommandCapabilities,
} from "@/lib/commands";
import { ApiError } from "@/lib/api/client";
import { DeviceCommandType } from "@/types/command";

interface CommandActionBarProps {
  deviceId: string;
  deviceName: string;
  /** Commands can only be issued to an ENROLLED device — see
   * app/commands/service.py::create_command's DEVICE_NOT_ENROLLED check. */
  enrollmentStatus: string;
  onError: (message: string) => void;
}

/**
 * First instance of a destructive-action confirmation pattern in this
 * codebase (no dialog/modal library exists anywhere here — see
 * docs/architecture/device-commands.md). A wipe (or unenroll) requires
 * typing the device's own display name before the button becomes
 * clickable, matching the plain-Tailwind, no-library style used
 * everywhere else. Any future destructive action should reuse this exact
 * shape.
 *
 * Phase 12: buttons are filtered by GET /commands/capabilities — an
 * unsupported command (e.g. REBOOT on an Apple device today) is hidden
 * entirely rather than shown and left to fail on click, per
 * docs/architecture/phase-12.md §2.
 */
export function CommandActionBar({
  deviceId,
  deviceName,
  enrollmentStatus,
  onError,
}: CommandActionBarProps) {
  const lockCommand = useIssueLockCommand();
  const rebootCommand = useIssueRebootCommand();
  const refreshCommand = useIssueRefreshInfoCommand();
  const policySyncCommand = useIssuePolicySyncCommand();
  const wipeCommand = useIssueWipeCommand();
  const unenrollCommand = useIssueUnenrollCommand();
  const { data: capabilities, isLoading: capabilitiesLoading } = useDeviceCommandCapabilities(
    enrollmentStatus === "ENROLLED" ? deviceId : null
  );

  const [confirmingWipe, setConfirmingWipe] = useState(false);
  const [wipeConfirmText, setWipeConfirmText] = useState("");
  const [confirmingUnenroll, setConfirmingUnenroll] = useState(false);
  const [unenrollConfirmText, setUnenrollConfirmText] = useState("");

  const runCommand = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Command failed.");
    }
  };

  if (enrollmentStatus !== "ENROLLED") {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Remote Actions</h2>
        <p className="mt-2 text-sm text-slate-500">
          This device is not enrolled — remote commands are unavailable until it enrolls.
        </p>
      </section>
    );
  }

  // Capabilities not loaded yet, or the provider itself isn't available
  // (e.g. missing real credentials) — don't show any action as
  // executable until we actually know it's supported.
  const supported = new Set<DeviceCommandType>(
    capabilities?.provider_available ? capabilities.supported_command_types : []
  );
  const supportsAny = supported.size > 0;

  const anyPending =
    lockCommand.isPending ||
    rebootCommand.isPending ||
    refreshCommand.isPending ||
    policySyncCommand.isPending ||
    wipeCommand.isPending ||
    unenrollCommand.isPending;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-slate-900">Remote Actions</h2>
      {capabilitiesLoading ? (
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          Checking supported actions…
        </p>
      ) : !supportsAny ? (
        <p className="text-sm text-slate-500">
          {capabilities?.provider_name
            ? `The ${capabilities.provider_name} provider is not available for this device — remote commands can't be issued right now.`
            : "No supported remote actions for this device."}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          {supported.has("LOCK") && (
            <button
              onClick={() => runCommand(() => lockCommand.mutateAsync({ deviceId, body: {} }))}
              disabled={anyPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
            >
              <Lock className="h-4 w-4" />
              Lock
            </button>
          )}
          {supported.has("REBOOT") && (
            <button
              onClick={() => runCommand(() => rebootCommand.mutateAsync({ deviceId, body: {} }))}
              disabled={anyPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-50"
            >
              <RotateCw className={`h-4 w-4 ${rebootCommand.isPending ? "animate-spin" : ""}`} />
              Reboot
            </button>
          )}
          {supported.has("REFRESH_INFO") && (
            <button
              onClick={() => runCommand(() => refreshCommand.mutateAsync({ deviceId, body: {} }))}
              disabled={anyPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${refreshCommand.isPending ? "animate-spin" : ""}`} />
              Refresh Info
            </button>
          )}
          {supported.has("POLICY_SYNC") && (
            <button
              onClick={() =>
                runCommand(() => policySyncCommand.mutateAsync({ deviceId, body: {} }))
              }
              disabled={anyPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <ShieldAlert className="h-4 w-4" />
              Sync Policy
            </button>
          )}

          <div className="ml-auto flex items-center gap-2">
            {supported.has("UNENROLL") &&
              (!confirmingUnenroll ? (
                <button
                  onClick={() => setConfirmingUnenroll(true)}
                  disabled={anyPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-orange-300 bg-white px-3 py-1.5 text-sm font-medium text-orange-700 hover:bg-orange-50 disabled:opacity-50"
                >
                  <Unlink className="h-4 w-4" />
                  Unenroll
                </button>
              ) : (
                <div className="flex items-center gap-2 rounded-lg border border-orange-300 bg-orange-50 p-2">
                  <span className="text-xs text-orange-800">
                    Type <strong>{deviceName}</strong> to confirm unenroll:
                  </span>
                  <input
                    autoFocus
                    value={unenrollConfirmText}
                    onChange={(e) => setUnenrollConfirmText(e.target.value)}
                    className="rounded border border-orange-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-orange-500"
                  />
                  <button
                    onClick={() =>
                      runCommand(async () => {
                        await unenrollCommand.mutateAsync({
                          deviceId,
                          body: { confirm: true, idempotency_key: crypto.randomUUID() },
                        });
                        setConfirmingUnenroll(false);
                        setUnenrollConfirmText("");
                      })
                    }
                    disabled={unenrollConfirmText !== deviceName || unenrollCommand.isPending}
                    className="rounded bg-orange-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-orange-700 disabled:opacity-40"
                  >
                    Confirm Unenroll
                  </button>
                  <button
                    onClick={() => {
                      setConfirmingUnenroll(false);
                      setUnenrollConfirmText("");
                    }}
                    className="rounded border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
                  >
                    Cancel
                  </button>
                </div>
              ))}

            {supported.has("WIPE") &&
              (!confirmingWipe ? (
                <button
                  onClick={() => setConfirmingWipe(true)}
                  disabled={anyPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                  Wipe
                </button>
              ) : (
                <div className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 p-2">
                  <span className="text-xs text-red-800">
                    Type <strong>{deviceName}</strong> to confirm wipe:
                  </span>
                  <input
                    autoFocus
                    value={wipeConfirmText}
                    onChange={(e) => setWipeConfirmText(e.target.value)}
                    className="rounded border border-red-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-red-500"
                  />
                  <button
                    onClick={() =>
                      runCommand(async () => {
                        await wipeCommand.mutateAsync({
                          deviceId,
                          body: { confirm: true, idempotency_key: crypto.randomUUID() },
                        });
                        setConfirmingWipe(false);
                        setWipeConfirmText("");
                      })
                    }
                    disabled={wipeConfirmText !== deviceName || wipeCommand.isPending}
                    className="rounded bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-40"
                  >
                    Confirm Wipe
                  </button>
                  <button
                    onClick={() => {
                      setConfirmingWipe(false);
                      setWipeConfirmText("");
                    }}
                    className="rounded border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
                  >
                    Cancel
                  </button>
                </div>
              ))}
          </div>
        </div>
      )}
    </section>
  );
}
