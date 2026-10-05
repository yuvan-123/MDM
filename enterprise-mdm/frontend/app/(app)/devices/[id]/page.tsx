"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Loader2, AlertCircle, ShieldOff, ShieldCheck, Archive, UserPlus, UserMinus, Check, X, Ban, RefreshCw } from "lucide-react";
import {
  useDevice,
  useAssignDevice,
  useUnassignDevice,
  useBlockDevice,
  useUnblockDevice,
  useRetireDevice,
  useApproveEnrollment,
  useRejectEnrollment,
  useRevokeEnrollment,
} from "@/lib/devices";
import { useOrganizationMembers } from "@/lib/organizations";
import { useActiveOrganizationId } from "@/lib/organization-store";
import { useDeviceCompliance, useEvaluateCompliance } from "@/lib/compliance";
import { useViolations } from "@/lib/violations";
import { useDeviceApplications } from "@/lib/applications";
import { ApiError } from "@/lib/api/client";
import { CommandActionBar } from "@/features/commands/CommandActionBar";
import { CommandHistorySection } from "@/features/commands/CommandHistorySection";

const STATUS_BADGE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  INACTIVE: "bg-slate-100 text-slate-500 border-slate-200",
  LOST: "bg-amber-50 text-amber-700 border-amber-200",
  BLOCKED: "bg-red-50 text-red-700 border-red-200",
  RETIRED: "bg-slate-100 text-slate-400 border-slate-200",
};

const CONNECTIVITY_BADGE: Record<string, string> = {
  ONLINE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  STALE: "bg-amber-50 text-amber-700 border-amber-200",
  OFFLINE: "bg-slate-100 text-slate-500 border-slate-200",
  NEVER_CHECKED_IN: "bg-slate-100 text-slate-400 border-slate-200",
};

const ENROLLMENT_BADGE: Record<string, string> = {
  NOT_ENROLLED: "bg-slate-100 text-slate-500 border-slate-200",
  PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  ENROLLED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REJECTED: "bg-red-50 text-red-700 border-red-200",
  REVOKED: "bg-red-50 text-red-700 border-red-200",
  EXPIRED: "bg-slate-100 text-slate-400 border-slate-200",
};

const COMPLIANCE_BADGE: Record<string, string> = {
  COMPLIANT: "bg-emerald-50 text-emerald-700 border-emerald-200",
  NON_COMPLIANT: "bg-red-50 text-red-700 border-red-200",
  UNKNOWN: "bg-amber-50 text-amber-700 border-amber-200",
};

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-900">{value ?? <span className="text-slate-400">—</span>}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      <dl className="mt-4 grid grid-cols-2 gap-4">{children}</dl>
    </section>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString();
}

export default function DeviceDetailPage() {
  const params = useParams<{ id: string }>();
  const deviceId = params.id;

  const { data: device, isLoading, isError } = useDevice(deviceId);
  const organizationId = useActiveOrganizationId();
  const { data: members } = useOrganizationMembers(organizationId);
  const activeMembers = (members ?? []).filter((m) => m.status === "ACTIVE");

  const assignDevice = useAssignDevice();
  const unassignDevice = useUnassignDevice();
  const blockDevice = useBlockDevice();
  const unblockDevice = useUnblockDevice();
  const retireDevice = useRetireDevice();
  const approveEnrollment = useApproveEnrollment();
  const rejectEnrollment = useRejectEnrollment();
  const revokeEnrollment = useRevokeEnrollment();

  const { data: compliance, isLoading: complianceLoading } = useDeviceCompliance(deviceId);
  const evaluateCompliance = useEvaluateCompliance();
  const { data: violations } = useViolations({ deviceId });
  const activeViolations = (violations?.items ?? []).filter((v) => v.status !== "RESOLVED");
  const { data: deviceApplications } = useDeviceApplications(deviceId);

  const [selectedUserId, setSelectedUserId] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const runAction = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    }
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (isError || !device) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <p className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          Device not found.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <div>
        <Link href="/devices" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to devices
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-bold text-slate-900">{device.display_name}</h1>
          <span
            className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              STATUS_BADGE[device.status] ?? "border-slate-200 text-slate-600"
            }`}
          >
            {device.status}
          </span>
          <span
            className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              CONNECTIVITY_BADGE[device.connectivity_state] ?? "border-slate-200 text-slate-600"
            }`}
          >
            {device.connectivity_state}
          </span>
          <span
            className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              device.management_state === "MANAGED"
                ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                : "border-slate-200 bg-slate-50 text-slate-500"
            }`}
          >
            {device.management_state === "MANAGED"
              ? `Managed via ${device.provider_name}`
              : "Unmanaged"}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Inventory record only — enrollment status reflects the MDM&apos;s own records, not
          real-time contact with the physical device.
          {device.management_state === "UNMANAGED" &&
            " No real provider identity is linked yet — remote commands may be limited until one is."}
        </p>
      </div>

      {actionError && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {actionError}
        </p>
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        {device.status !== "RETIRED" && device.status !== "BLOCKED" && (
          <button
            onClick={() => runAction(() => blockDevice.mutateAsync({ deviceId }))}
            disabled={blockDevice.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            <ShieldOff className="h-4 w-4" />
            Block
          </button>
        )}
        {device.status === "BLOCKED" && (
          <button
            onClick={() => runAction(() => unblockDevice.mutateAsync({ deviceId }))}
            disabled={unblockDevice.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
          >
            <ShieldCheck className="h-4 w-4" />
            Unblock
          </button>
        )}
        {device.status !== "RETIRED" && (
          <button
            onClick={() => runAction(() => retireDevice.mutateAsync({ deviceId }))}
            disabled={retireDevice.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            <Archive className="h-4 w-4" />
            Retire
          </button>
        )}
        {device.enrollment_status === "PENDING" && (
          <>
            <button
              onClick={() => runAction(() => approveEnrollment.mutateAsync({ deviceId }))}
              disabled={approveEnrollment.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              Approve enrollment
            </button>
            <button
              onClick={() => runAction(() => rejectEnrollment.mutateAsync({ deviceId }))}
              disabled={rejectEnrollment.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
              Reject enrollment
            </button>
          </>
        )}
        {device.enrollment_status === "ENROLLED" && (
          <button
            onClick={() => runAction(() => revokeEnrollment.mutateAsync({ deviceId }))}
            disabled={revokeEnrollment.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            <Ban className="h-4 w-4" />
            Revoke enrollment
          </button>
        )}

        <div className="ml-auto flex items-center gap-2">
          {device.assigned_user_id ? (
            <button
              onClick={() => runAction(() => unassignDevice.mutateAsync({ deviceId }))}
              disabled={unassignDevice.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <UserMinus className="h-4 w-4" />
              Unassign
            </button>
          ) : (
            <>
              <select
                value={selectedUserId}
                onChange={(e) => setSelectedUserId(e.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
              >
                <option value="">Select member…</option>
                {activeMembers.map((m) => (
                  <option key={m.id} value={m.user_id}>
                    {m.user_full_name} ({m.user_email})
                  </option>
                ))}
              </select>
              <button
                onClick={() =>
                  runAction(() =>
                    assignDevice.mutateAsync({ deviceId, body: { user_id: selectedUserId } })
                  )
                }
                disabled={assignDevice.isPending || !selectedUserId || device.status === "RETIRED"}
                className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                <UserPlus className="h-4 w-4" />
                Assign
              </button>
            </>
          )}
        </div>
      </section>

      <CommandActionBar
        deviceId={deviceId}
        deviceName={device.display_name}
        enrollmentStatus={device.enrollment_status}
        onError={setActionError}
      />

      <Section title="Overview">
        <Field label="Display name" value={device.display_name} />
        <Field label="Device type" value={device.device_type} />
        <Field label="Manufacturer" value={device.manufacturer} />
        <Field label="Model" value={device.model} />
      </Section>

      <Section title="Platform">
        <Field label="Platform" value={device.platform} />
        <Field label="Operating system" value={device.operating_system} />
        <Field label="OS version" value={device.os_version} />
        <Field label="Security patch level" value={device.security_patch_level} />
      </Section>

      <Section title="Identity">
        <Field label="Serial number" value={device.serial_number} />
        <Field label="Device identifier" value={device.device_identifier} />
        <Field
          label="IMEI (last 4)"
          value={device.imei_last4 ? `••••${device.imei_last4}` : "Not visible — requires device.view_sensitive"}
        />
        <Field
          label="Wi-Fi MAC (last 4)"
          value={device.wifi_mac_last4 ? `••:••:${device.wifi_mac_last4}` : "Not visible — requires device.view_sensitive"}
        />
      </Section>

      <Section title="Ownership">
        <Field label="Ownership type" value={device.ownership_type} />
      </Section>

      <Section title="Assignment">
        <Field label="Assigned user" value={device.assigned_user_full_name} />
        <Field label="Email" value={device.assigned_user_email} />
      </Section>

      <Section title="Enrollment">
        <Field
          label="Enrollment status"
          value={
            <span
              className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                ENROLLMENT_BADGE[device.enrollment_status] ?? "border-slate-200 text-slate-600"
              }`}
            >
              {device.enrollment_status}
            </span>
          }
        />
        <Field label="Enrolled at" value={formatDate(device.enrolled_at) || null} />
        <Field label="Last enrollment attempt" value={formatDate(device.last_enrollment_attempt_at) || null} />
      </Section>

      <Section title="Reported Health">
        <Field label="Client version" value={device.client_version} />
        <Field
          label="Encrypted"
          value={device.is_encrypted === null ? null : device.is_encrypted ? "Yes" : "No"}
        />
        <Field
          label="Screen lock"
          value={device.has_screen_lock === null ? null : device.has_screen_lock ? "Yes" : "No"}
        />
        <Field
          label="Rooted / jailbroken"
          value={device.is_rooted === null ? null : device.is_rooted ? "Yes" : "No"}
        />
      </Section>

      <Section title="Lifecycle">
        <Field label="Status" value={device.status} />
        <Field label="Last seen" value={formatDate(device.last_seen_at) || null} />
        <Field label="Last check-in" value={formatDate(device.last_check_in_at) || null} />
        <Field label="Created" value={formatDate(device.created_at)} />
        <Field label="Updated" value={formatDate(device.updated_at)} />
      </Section>

      <CommandHistorySection deviceId={deviceId} onError={setActionError} />

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Compliance</h2>
          <button
            onClick={() => runAction(() => evaluateCompliance.mutateAsync(deviceId))}
            disabled={evaluateCompliance.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${evaluateCompliance.isPending ? "animate-spin" : ""}`} />
            Re-evaluate
          </button>
        </div>
        {complianceLoading ? (
          <Loader2 className="mt-3 h-4 w-4 animate-spin text-slate-400" />
        ) : compliance ? (
          <div className="mt-3 space-y-2">
            <span
              className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                COMPLIANCE_BADGE[compliance.status] ?? "border-slate-200 text-slate-600"
              }`}
            >
              {compliance.status}
            </span>
            <ul className="space-y-1">
              {compliance.details.map((d) => (
                <li key={d.code} className="text-xs text-slate-500">
                  <span
                    className={
                      d.outcome === "FAIL"
                        ? "text-red-600"
                        : d.outcome === "UNKNOWN"
                          ? "text-amber-600"
                          : "text-emerald-600"
                    }
                  >
                    {d.outcome}
                  </span>{" "}
                  — {d.message}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">Not yet evaluated.</p>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Applications</h2>
          <Link
            href="/applications"
            className="text-xs font-medium text-slate-500 hover:text-slate-700"
          >
            Catalog →
          </Link>
        </div>
        {(deviceApplications?.items ?? []).length > 0 ? (
          <ul className="mt-3 divide-y divide-slate-100">
            {(deviceApplications?.items ?? []).map((item) => (
              <li key={item.application.id} className="flex items-center justify-between py-2.5">
                <div>
                  <Link
                    href={`/applications/${item.application.id}`}
                    className="text-sm font-medium text-slate-900 hover:underline"
                  >
                    {item.application.name}
                  </Link>
                  <p className="text-xs text-slate-400">
                    {item.requirement}
                    {item.deployment?.last_error ? ` · ${item.deployment.last_error}` : ""}
                  </p>
                </div>
                <span
                  className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                    item.deployment?.status === "INSTALLED"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : item.deployment?.status === "FAILED"
                        ? "border-red-200 bg-red-50 text-red-700"
                        : item.deployment?.status === "INSTALLING"
                          ? "border-blue-200 bg-blue-50 text-blue-700"
                          : "border-slate-200 bg-slate-50 text-slate-500"
                  }`}
                >
                  {item.deployment?.status ?? "NOT DEPLOYED"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">No applications assigned to this device.</p>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Active Violations</h2>
          <Link
            href="/compliance/violations"
            className="text-xs font-medium text-slate-500 hover:text-slate-700"
          >
            Manage all →
          </Link>
        </div>
        {activeViolations.length > 0 ? (
          <ul className="mt-3 divide-y divide-slate-100">
            {activeViolations.map((v) => (
              <li key={v.id} className="py-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">{v.rule_code}</span>
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-600">
                    {v.status.replace(/_/g, " ")}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-slate-500">
                  {v.message} · attempts {v.remediation_attempts}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">No active violations.</p>
        )}
      </section>
    </main>
  );
}
