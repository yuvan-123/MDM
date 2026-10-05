"use client";

import Link from "next/link";
import { Loader2, AlertCircle, CheckCircle2, XCircle, HelpCircle, Smartphone } from "lucide-react";
import { useComplianceSummary, useNonCompliantDevices } from "@/lib/compliance";

function StatTile({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: React.ElementType;
  tone: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        <Icon className={`h-4 w-4 ${tone}`} />
      </div>
      <p className="mt-2 text-2xl font-bold text-slate-900">{value}</p>
    </div>
  );
}

export default function CompliancePage() {
  const { data: summary, isLoading, isError } = useComplianceSummary();
  const { data: nonCompliant } = useNonCompliantDevices();

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Compliance</h1>
          <p className="mt-1 text-sm text-slate-600">
            Evaluated against each device&apos;s resolved policy (device → device group → user → organization).
          </p>
        </div>
        <Link
          href="/compliance/violations"
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          View violations
        </Link>
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-slate-500">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          Loading summary…
        </div>
      ) : isError || !summary ? (
        <p className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          Failed to load compliance summary.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <StatTile label="Total devices" value={summary.total_devices} icon={Smartphone} tone="text-slate-500" />
          <StatTile label="Compliant" value={summary.compliant} icon={CheckCircle2} tone="text-emerald-600" />
          <StatTile label="Non-compliant" value={summary.non_compliant} icon={XCircle} tone="text-red-600" />
          <StatTile label="Unknown" value={summary.unknown} icon={HelpCircle} tone="text-amber-600" />
          <StatTile label="Not evaluated" value={summary.not_evaluated} icon={HelpCircle} tone="text-slate-400" />
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <h2 className="px-6 pt-6 text-sm font-semibold text-slate-900">Non-compliant devices</h2>
        {nonCompliant && nonCompliant.items.length > 0 ? (
          <ul className="mt-2 divide-y divide-slate-100">
            {nonCompliant.items.map((item) => (
              <li key={item.device_id} className="px-6 py-4">
                <div className="flex items-center justify-between">
                  <Link
                    href={`/devices/${item.device_id}`}
                    className="text-sm font-medium text-slate-900 hover:underline"
                  >
                    {item.device_id}
                  </Link>
                  <span className="rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700">
                    NON_COMPLIANT
                  </span>
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {item.details
                    .filter((d) => d.outcome === "FAIL")
                    .map((d) => (
                      <li key={d.code} className="text-xs text-slate-500">
                        {d.message}
                      </li>
                    ))}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No non-compliant devices.</p>
        )}
      </section>
    </main>
  );
}
