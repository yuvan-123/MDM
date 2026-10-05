"use client";

import { Loader2, AlertCircle, CheckCircle2, XCircle, MinusCircle } from "lucide-react";
import { useProviderStatus } from "@/lib/provider-status";
import { ApiError } from "@/lib/api/client";
import { ProviderHealth } from "@/types/provider-status";

function HealthCard({ name, health }: { name: string; health: ProviderHealth }) {
  const icon = !health.configured ? (
    <MinusCircle className="h-5 w-5 text-slate-400" />
  ) : health.ok ? (
    <CheckCircle2 className="h-5 w-5 text-emerald-600" />
  ) : (
    <XCircle className="h-5 w-5 text-red-600" />
  );

  const label = !health.configured ? "Not configured" : health.ok ? "Healthy" : "Unhealthy";
  const labelColor = !health.configured
    ? "text-slate-500"
    : health.ok
      ? "text-emerald-700"
      : "text-red-700";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold capitalize text-slate-900">{name}</h2>
        {icon}
      </div>
      <p className={`mt-2 text-sm font-medium ${labelColor}`}>{label}</p>
      {health.error && <p className="mt-1 text-xs text-red-600">{health.error}</p>}
      {!health.configured && (
        <p className="mt-1 text-xs text-slate-400">
          No {name} credentials are set for this deployment.
        </p>
      )}
    </div>
  );
}

export default function ProvidersStatusPage() {
  const { data, isLoading, isError, error } = useProviderStatus();

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Provider Health</h1>
        <p className="mt-1 text-sm text-slate-600">
          Deployment-wide Android/Apple credential and connectivity status. Never reflects a
          specific organization or device — Android/Apple credentials are configured once per
          deployment, not per organization. Requires a platform-level role; see{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/phase-10.md
          </code>
          .
        </p>
      </div>

      {isLoading ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          Loading provider status…
        </div>
      ) : isError ? (
        <p className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {error instanceof ApiError
            ? error.message
            : "Failed to load provider status."}
        </p>
      ) : data ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <HealthCard name="android" health={data.android} />
          <HealthCard name="apple" health={data.apple} />
        </div>
      ) : null}
    </main>
  );
}
