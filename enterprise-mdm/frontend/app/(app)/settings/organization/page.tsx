"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { Building2, Loader2, AlertCircle } from "lucide-react";
import { useCurrentOrganizationContext } from "@/lib/organizations";
import {
  subscribeActiveOrganizationId,
  getSnapshot,
  getServerSnapshot,
} from "@/lib/organization-store";

export default function CurrentOrganizationSettingsPage() {
  const activeOrganizationId = useSyncExternalStore(
    subscribeActiveOrganizationId,
    getSnapshot,
    getServerSnapshot
  );
  const { data: context, isLoading, isError } = useCurrentOrganizationContext();

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Organization Settings</h1>
        <p className="mt-1 text-sm text-slate-600">
          The organization currently selected in the header switcher.
        </p>
      </div>

      {!activeOrganizationId ? (
        <p className="flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700">
          <AlertCircle className="h-4 w-4" />
          No organization selected — choose one from the switcher in the header.
        </p>
      ) : isLoading ? (
        <p className="text-sm text-slate-500">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          Loading…
        </p>
      ) : isError || !context ? (
        <p className="text-sm text-red-600">Failed to load organization context.</p>
      ) : (
        <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
              <Building2 className="h-5 w-5" />
            </div>
            <div>
              <p className="text-lg font-semibold text-slate-900">{context.organization.name}</p>
              <p className="text-sm text-slate-500">{context.organization.slug}</p>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-500">Status</dt>
              <dd className="font-medium text-slate-900">{context.organization.status}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Your role</dt>
              <dd className="font-medium text-slate-900">
                {context.is_platform_super_admin ? "Platform Super Admin" : "Member"}
              </dd>
            </div>
          </dl>
          <div>
            <dt className="text-xs font-medium text-slate-500">Effective permissions</dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {context.permissions.length > 0 ? (
                context.permissions.map((perm) => (
                  <span
                    key={perm}
                    className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600"
                  >
                    {perm}
                  </span>
                ))
              ) : (
                <span className="text-xs text-slate-400">
                  Full access via platform super admin.
                </span>
              )}
            </dd>
          </div>
          <Link
            href={`/organizations/${context.organization.id}`}
            className="inline-block text-sm text-slate-600 hover:text-slate-900"
          >
            Manage organization →
          </Link>
        </section>
      )}
    </main>
  );
}
