"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Loader2, AlertCircle, Trash2, Plus } from "lucide-react";
import { usePolicy, usePolicyVersions, usePolicyAssignments, useUpdatePolicy, useAssignPolicy, useUnassignPolicy } from "@/lib/policies";
import { useDeviceGroups } from "@/lib/device-groups";
import { useOrganizationMembers } from "@/lib/organizations";
import { useActiveOrganizationId } from "@/lib/organization-store";
import { ApiError } from "@/lib/api/client";
import {
  DeviceSecurityPolicyConfig,
  PolicyScopeType,
  RemediationCommandType,
  RemediationMode,
  RemediationPolicyConfig,
} from "@/types/policy";

const REMEDIATION_COMMAND_TYPES: RemediationCommandType[] = ["REFRESH_INFO", "POLICY_SYNC", "LOCK"];

export default function PolicyDetailPage() {
  const params = useParams<{ id: string }>();
  const policyId = params.id;
  const organizationId = useActiveOrganizationId();

  const { data: policy, isLoading, isError } = usePolicy(policyId);
  const { data: versions } = usePolicyVersions(policyId);
  const { data: assignments } = usePolicyAssignments(policyId);
  const { data: groups } = useDeviceGroups();
  const { data: members } = useOrganizationMembers(organizationId);

  const updatePolicy = useUpdatePolicy(policyId);
  const assignPolicy = useAssignPolicy(policyId);
  const unassignPolicy = useUnassignPolicy(policyId);

  const [config, setConfig] = useState<DeviceSecurityPolicyConfig>({});
  const [remediation, setRemediation] = useState<RemediationPolicyConfig>({
    mode: "NONE",
    max_attempts: 3,
  });
  const [error, setError] = useState<string | null>(null);
  const [scopeType, setScopeType] = useState<PolicyScopeType>("ORGANIZATION");
  const [scopeId, setScopeId] = useState("");

  useEffect(() => {
    if (policy?.current_version?.configuration.device_security) {
      setConfig(policy.current_version.configuration.device_security);
    }
    if (policy?.current_version?.configuration.remediation) {
      setRemediation(policy.current_version.configuration.remediation);
    }
  }, [policy]);

  const runAction = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    }
  };

  // A new PolicyVersion always replaces the ENTIRE configuration blob
  // (see app/policies/service.py::update_policy) — never just the one
  // section being edited, or every other already-configured section
  // (remediation, password, etc.) would be silently dropped.
  const handleSaveConfig = () =>
    runAction(() =>
      updatePolicy.mutateAsync({
        configuration: { ...policy?.current_version?.configuration, device_security: config },
      })
    );

  const handleSaveRemediation = () =>
    runAction(() =>
      updatePolicy.mutateAsync({
        configuration: { ...policy?.current_version?.configuration, remediation },
      })
    );

  const handleAssign = () => {
    if (!scopeId) return;
    runAction(() => assignPolicy.mutateAsync({ scope_type: scopeType, scope_id: scopeId }));
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (isError || !policy) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <p className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          Policy not found.
        </p>
      </main>
    );
  }

  const scopeOptions =
    scopeType === "ORGANIZATION"
      ? [{ id: organizationId ?? "", label: "This organization" }]
      : scopeType === "DEVICE_GROUP"
        ? (groups?.items ?? []).map((g) => ({ id: g.id, label: g.name }))
        : scopeType === "USER"
          ? (members ?? []).map((m) => ({ id: m.user_id, label: m.user_full_name }))
          : [];

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <div>
        <Link href="/policies" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to policies
        </Link>
        <div className="mt-2 flex items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{policy.name}</h1>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-slate-600">
            {policy.status}
          </span>
        </div>
        <p className="mt-1 text-sm text-slate-600">{policy.description}</p>
      </div>

      {error && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {error}
        </p>
      )}

      <section className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        {policy.status === "DRAFT" && (
          <button
            onClick={() => runAction(() => updatePolicy.mutateAsync({ status: "ACTIVE" }))}
            className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
          >
            Activate
          </button>
        )}
        {policy.status === "ACTIVE" && (
          <button
            onClick={() => runAction(() => updatePolicy.mutateAsync({ status: "DRAFT" }))}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Move to draft
          </button>
        )}
        <span className="text-xs text-slate-400">
          Current version: v{policy.current_version?.version ?? "—"}
        </span>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Device security configuration</h2>
        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={!!config.require_encryption}
              onChange={(e) => setConfig({ ...config, require_encryption: e.target.checked })}
              className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-800"
            />
            Require encryption
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={!!config.require_screen_lock}
              onChange={(e) => setConfig({ ...config, require_screen_lock: e.target.checked })}
              className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-800"
            />
            Require screen lock
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={!!config.block_rooted_jailbroken}
              onChange={(e) => setConfig({ ...config, block_rooted_jailbroken: e.target.checked })}
              className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-800"
            />
            Block rooted / jailbroken devices
          </label>
          <div className="flex items-center gap-3">
            <label className="text-sm text-slate-700">Minimum OS version</label>
            <input
              type="text"
              value={config.minimum_os_version ?? ""}
              onChange={(e) => setConfig({ ...config, minimum_os_version: e.target.value || null })}
              placeholder="e.g. 14"
              className="w-32 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
        </div>
        <button
          onClick={handleSaveConfig}
          disabled={updatePolicy.isPending}
          className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
        >
          Save (creates new version)
        </button>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Automated remediation</h2>
        <p className="mt-1 text-xs text-slate-400">
          What to do when a device violates this policy. Destructive actions (wipe, unenroll) are
          never offered here — only refresh, policy sync, and lock are supported remediation
          commands. See{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/phase-13.md
          </code>
          .
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-600">Mode</label>
            <select
              value={remediation.mode}
              onChange={(e) =>
                setRemediation({
                  ...remediation,
                  mode: e.target.value as RemediationMode,
                  command_type: e.target.value === "COMMAND" ? remediation.command_type : null,
                })
              }
              className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              <option value="NONE">None — track violations only</option>
              <option value="NOTIFY">Notify — flag for admin attention</option>
              <option value="COMMAND">Command — issue a remote command automatically</option>
            </select>
          </div>
          {remediation.mode === "COMMAND" && (
            <>
              <div>
                <label className="block text-xs font-medium text-slate-600">Command</label>
                <select
                  value={remediation.command_type ?? ""}
                  onChange={(e) =>
                    setRemediation({
                      ...remediation,
                      command_type: e.target.value as RemediationCommandType,
                    })
                  }
                  className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
                >
                  <option value="" disabled>
                    Select a command…
                  </option>
                  {REMEDIATION_COMMAND_TYPES.map((c) => (
                    <option key={c} value={c}>
                      {c.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-700">Max automatic attempts</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={remediation.max_attempts}
                  onChange={(e) =>
                    setRemediation({ ...remediation, max_attempts: Number(e.target.value) })
                  }
                  className="w-20 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
                />
              </div>
            </>
          )}
        </div>
        <button
          onClick={handleSaveRemediation}
          disabled={
            updatePolicy.isPending || (remediation.mode === "COMMAND" && !remediation.command_type)
          }
          className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
        >
          Save (creates new version)
        </button>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Version history</h2>
        <ul className="mt-3 space-y-1.5">
          {(versions ?? []).map((v) => (
            <li key={v.id} className="text-xs text-slate-500">
              v{v.version} — {new Date(v.created_at).toLocaleString()}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Assignments</h2>
        <div className="mt-3 flex items-end gap-2">
          <select
            value={scopeType}
            onChange={(e) => {
              setScopeType(e.target.value as PolicyScopeType);
              setScopeId("");
            }}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            <option value="ORGANIZATION">Organization</option>
            <option value="DEVICE_GROUP">Device group</option>
            <option value="USER">User</option>
            <option value="DEVICE">Device (by ID)</option>
          </select>
          {scopeType === "DEVICE" ? (
            <input
              type="text"
              value={scopeId}
              onChange={(e) => setScopeId(e.target.value)}
              placeholder="Paste device ID from its detail page"
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          ) : (
            <select
              value={scopeId}
              onChange={(e) => setScopeId(e.target.value)}
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              <option value="">Select target…</option>
              {scopeOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={handleAssign}
            disabled={assignPolicy.isPending || !scopeId}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            <Plus className="h-4 w-4" />
            Assign
          </button>
        </div>

        <ul className="mt-4 divide-y divide-slate-100">
          {(assignments ?? []).map((a) => (
            <li key={a.id} className="flex items-center justify-between py-2">
              <span className="text-sm text-slate-700">
                {a.scope_type} — <code className="text-xs text-slate-500">{a.scope_id}</code>
              </span>
              <button
                onClick={() =>
                  runAction(() =>
                    unassignPolicy.mutateAsync({ scope_type: a.scope_type, scope_id: a.scope_id })
                  )
                }
                className="text-slate-400 hover:text-red-600"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
          {(assignments ?? []).length === 0 && (
            <p className="py-2 text-sm text-slate-500">Not assigned anywhere yet.</p>
          )}
        </ul>
      </section>
    </main>
  );
}
