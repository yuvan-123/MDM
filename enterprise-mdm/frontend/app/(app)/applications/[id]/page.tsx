"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Loader2, AlertCircle, Trash2, Plus, Rocket } from "lucide-react";
import {
  useApplication,
  useApplicationAssignments,
  useApplicationDeployments,
  useUpdateApplication,
  useDeleteApplication,
  useAssignApplication,
  useUnassignApplication,
  useDeployApplication,
} from "@/lib/applications";
import { ApiError } from "@/lib/api/client";
import { ApplicationRequirement, ApplicationDeploymentStatus } from "@/types/application";
import { PolicyScopeType } from "@/types/policy";

const SCOPE_TYPES: PolicyScopeType[] = ["ORGANIZATION", "USER", "DEVICE_GROUP", "DEVICE"];
const REQUIREMENTS: ApplicationRequirement[] = ["OPTIONAL", "REQUIRED"];

const DEPLOYMENT_BADGE: Record<ApplicationDeploymentStatus, string> = {
  PENDING: "bg-slate-100 text-slate-500 border-slate-200",
  INSTALLING: "bg-blue-50 text-blue-700 border-blue-200",
  INSTALLED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  REMOVED: "bg-slate-100 text-slate-400 border-slate-200",
};

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export default function ApplicationDetailPage() {
  const params = useParams<{ id: string }>();
  const applicationId = params.id;

  const { data: application, isLoading, isError } = useApplication(applicationId);
  const { data: assignments } = useApplicationAssignments(applicationId);
  const { data: deployments } = useApplicationDeployments(applicationId);

  const updateApplication = useUpdateApplication(applicationId);
  const deleteApplication = useDeleteApplication();
  const assignApplication = useAssignApplication(applicationId);
  const unassignApplication = useUnassignApplication(applicationId);
  const deployApplication = useDeployApplication(applicationId);

  const [scopeType, setScopeType] = useState<PolicyScopeType>("DEVICE");
  const [scopeId, setScopeId] = useState("");
  const [requirement, setRequirement] = useState<ApplicationRequirement>("OPTIONAL");
  const [deployDeviceId, setDeployDeviceId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const runAction = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    }
  };

  const handleAssign = () => {
    if (!scopeId.trim()) return;
    runAction(() =>
      assignApplication.mutateAsync({ scope_type: scopeType, scope_id: scopeId.trim(), requirement })
    );
  };

  const handleDeploy = () => {
    if (!deployDeviceId.trim()) return;
    runAction(() => deployApplication.mutateAsync({ device_id: deployDeviceId.trim() }));
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (isError || !application) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <p className="text-sm text-red-600">Failed to load application.</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">
      <div>
        <Link href="/applications" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to applications
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{application.name}</h1>
            <p className="mt-1 text-sm text-slate-600">
              {application.platform} · {application.package_name || application.bundle_identifier}
            </p>
          </div>
          <button
            onClick={() =>
              runAction(async () => {
                await deleteApplication.mutateAsync(applicationId);
                window.location.href = "/applications";
              })
            }
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {error}
        </p>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Details</h2>
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500">Version</label>
            <input
              type="text"
              defaultValue={application.version ?? ""}
              onBlur={(e) => runAction(() => updateApplication.mutateAsync({ version: e.target.value }))}
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500">Publisher</label>
            <input
              type="text"
              defaultValue={application.publisher ?? ""}
              onBlur={(e) =>
                runAction(() => updateApplication.mutateAsync({ publisher: e.target.value }))
              }
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Assignments</h2>
        <div className="mt-4 flex items-end gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-600">Scope</label>
            <select
              value={scopeType}
              onChange={(e) => setScopeType(e.target.value as PolicyScopeType)}
              className="mt-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              {SCOPE_TYPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Scope ID</label>
            <input
              type="text"
              value={scopeId}
              onChange={(e) => setScopeId(e.target.value)}
              placeholder="device/user/group/organization UUID"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Requirement</label>
            <select
              value={requirement}
              onChange={(e) => setRequirement(e.target.value as ApplicationRequirement)}
              className="mt-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              {REQUIREMENTS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleAssign}
            disabled={assignApplication.isPending || !scopeId.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Assign
          </button>
        </div>

        <ul className="mt-4 divide-y divide-slate-100">
          {(assignments ?? []).map((a) => (
            <li key={a.id} className="flex items-center justify-between py-2.5">
              <div>
                <span className="text-sm text-slate-900">
                  {a.scope_type} · {a.scope_id}
                </span>
                <span className="ml-2 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-600">
                  {a.requirement}
                </span>
              </div>
              <button
                onClick={() =>
                  runAction(() =>
                    unassignApplication.mutateAsync({
                      scope_type: a.scope_type,
                      scope_id: a.scope_id,
                    })
                  )
                }
                className="text-xs font-medium text-red-600 hover:text-red-700"
              >
                Unassign
              </button>
            </li>
          ))}
          {(assignments ?? []).length === 0 && (
            <p className="py-2.5 text-sm text-slate-500">No assignments yet.</p>
          )}
        </ul>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Deploy</h2>
        <p className="mt-1 text-xs text-slate-500">
          Dispatches an INSTALL_APPLICATION command to one device — see{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/phase-14.md
          </code>{" "}
          for bulk (device group / organization-wide) targets via the API.
        </p>
        <div className="mt-3 flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Device ID</label>
            <input
              type="text"
              value={deployDeviceId}
              onChange={(e) => setDeployDeviceId(e.target.value)}
              placeholder="Device UUID"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <button
            onClick={handleDeploy}
            disabled={deployApplication.isPending || !deployDeviceId.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
          >
            <Rocket className="h-3.5 w-3.5" />
            Deploy
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <h2 className="px-6 pt-6 text-sm font-semibold text-slate-900">Deployment history</h2>
        {(deployments?.items ?? []).length > 0 ? (
          <ul className="mt-2 divide-y divide-slate-100">
            {(deployments?.items ?? []).map((d) => (
              <li key={d.id} className="px-6 py-3">
                <div className="flex items-center justify-between">
                  <Link
                    href={`/devices/${d.device_id}`}
                    className="text-sm font-medium text-slate-900 hover:underline"
                  >
                    {d.device_id}
                  </Link>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${DEPLOYMENT_BADGE[d.status]}`}
                  >
                    {d.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {d.requirement} · {d.attempts} attempt{d.attempts === 1 ? "" : "s"} · updated{" "}
                  {formatDate(d.updated_at)}
                  {d.last_error ? ` · ${d.last_error}` : ""}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No deployments yet.</p>
        )}
      </section>
    </main>
  );
}
