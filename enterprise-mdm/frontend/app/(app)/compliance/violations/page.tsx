"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, AlertCircle, RotateCcw, Check, Ban, PlayCircle } from "lucide-react";
import {
  useViolations,
  useRetryRemediation,
  useResolveViolation,
  useSetRemediationDisabled,
} from "@/lib/violations";
import { ApiError } from "@/lib/api/client";
import { ViolationStatus } from "@/types/violation";

const STATUSES: ViolationStatus[] = [
  "OPEN",
  "REMEDIATION_PENDING",
  "REMEDIATING",
  "RESOLVED",
  "REMEDIATION_FAILED",
];

const STATUS_BADGE: Record<ViolationStatus, string> = {
  OPEN: "bg-amber-50 text-amber-700 border-amber-200",
  REMEDIATION_PENDING: "bg-blue-50 text-blue-700 border-blue-200",
  REMEDIATING: "bg-blue-50 text-blue-700 border-blue-200",
  RESOLVED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REMEDIATION_FAILED: "bg-red-50 text-red-700 border-red-200",
};

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export default function PolicyViolationsPage() {
  const [status, setStatus] = useState<ViolationStatus | "">("");
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, isLoading, isError } = useViolations({ status: status || undefined });
  const retryRemediation = useRetryRemediation();
  const resolveViolation = useResolveViolation();
  const setRemediationDisabled = useSetRemediationDisabled();

  const runAction = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    }
  };

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-8">
      <div>
        <Link href="/compliance" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to compliance
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-slate-900">Policy Violations</h1>
        <p className="mt-1 text-sm text-slate-600">
          One row per (device, policy, rule) that has failed — tracked and, where the policy
          configures it, automatically remediated through a remote command. See{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/phase-13.md
          </code>
          .
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as ViolationStatus | "")}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {actionError && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {actionError}
        </p>
      )}

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading violations…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load violations.</p>
        ) : data && data.items.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {data.items.map((v) => (
              <li key={v.id} className="px-6 py-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-slate-900">{v.rule_code}</span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[v.status]}`}
                      >
                        {v.status.replace(/_/g, " ")}
                      </span>
                      {v.automated_remediation_disabled && (
                        <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-500">
                          remediation disabled
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{v.message}</p>
                    <p className="mt-0.5 text-xs text-slate-400">
                      expected <code>{v.expected_value ?? "—"}</code> · actual{" "}
                      <code>{v.current_value ?? "—"}</code> · attempts {v.remediation_attempts} ·
                      last seen {formatDate(v.last_seen_at)}
                    </p>
                    <Link
                      href={`/devices/${v.device_id}`}
                      className="mt-1 inline-block text-xs text-slate-500 hover:underline"
                    >
                      View device →
                    </Link>
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    {v.status === "REMEDIATION_FAILED" && (
                      <button
                        onClick={() => runAction(() => retryRemediation.mutateAsync(v.id))}
                        disabled={retryRemediation.isPending}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        Retry
                      </button>
                    )}
                    {v.status !== "RESOLVED" && (
                      <button
                        onClick={() => runAction(() => resolveViolation.mutateAsync(v.id))}
                        disabled={resolveViolation.isPending}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        <Check className="h-3.5 w-3.5" />
                        Resolve
                      </button>
                    )}
                    {v.status !== "RESOLVED" &&
                      (v.automated_remediation_disabled ? (
                        <button
                          onClick={() =>
                            runAction(() =>
                              setRemediationDisabled.mutateAsync({
                                violationId: v.id,
                                disabled: false,
                              })
                            )
                          }
                          disabled={setRemediationDisabled.isPending}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          <PlayCircle className="h-3.5 w-3.5" />
                          Enable automation
                        </button>
                      ) : (
                        <button
                          onClick={() =>
                            runAction(() =>
                              setRemediationDisabled.mutateAsync({
                                violationId: v.id,
                                disabled: true,
                              })
                            )
                          }
                          disabled={setRemediationDisabled.isPending}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          <Ban className="h-3.5 w-3.5" />
                          Disable automation
                        </button>
                      ))}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No violations match this filter.</p>
        )}
      </section>
    </main>
  );
}
