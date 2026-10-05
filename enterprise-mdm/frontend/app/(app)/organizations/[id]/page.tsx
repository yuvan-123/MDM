"use client";

import { useState, FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Building2, Users, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import {
  useOrganization,
  useUpdateOrganization,
  useUpdateOrganizationStatus,
} from "@/lib/organizations";
import { ApiError } from "@/lib/api/client";
import { OrganizationStatus } from "@/types/organization";

const STATUS_OPTIONS: OrganizationStatus[] = ["ACTIVE", "SUSPENDED", "DISABLED"];

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const organizationId = params.id;
  const router = useRouter();

  const { data: org, isLoading, isError } = useOrganization(organizationId);
  const updateOrg = useUpdateOrganization(organizationId);
  const updateStatus = useUpdateOrganizationStatus(organizationId);

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleRename = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (!name.trim()) return;
    try {
      await updateOrg.mutateAsync({ name: name.trim() });
      setSuccess("Organization updated.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update organization.");
    }
  };

  const handleStatusChange = async (status: OrganizationStatus) => {
    setError(null);
    setSuccess(null);
    try {
      await updateStatus.mutateAsync({ status });
      setSuccess(`Organization status set to ${status}.`);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to update status (platform administrators only)."
      );
    }
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8 text-sm text-slate-500">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
        Loading organization…
      </main>
    );
  }

  if (isError || !org) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <p className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          You do not have access to this organization, or it does not exist.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
            <Building2 className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{org.name}</h1>
            <p className="text-sm text-slate-500">{org.slug}</p>
          </div>
        </div>
        <button
          onClick={() => router.push(`/organizations/${organizationId}/members`)}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
        >
          <Users className="h-4 w-4" />
          Members
        </button>
      </div>

      {(error || success) && (
        <div
          role="alert"
          className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-red-200 bg-red-50 text-red-700"
              : "border-emerald-200 bg-emerald-50 text-emerald-700"
          }`}
        >
          {error ? <AlertCircle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
          {error ?? success}
        </div>
      )}

      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Details</h2>
        <form onSubmit={handleRename} className="flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Name</label>
            <input
              type="text"
              defaultValue={org.name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <button
            type="submit"
            disabled={updateOrg.isPending}
            className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-70"
          >
            Save
          </button>
        </form>
      </section>

      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Status</h2>
          <p className="text-xs text-slate-500">
            Platform administrators only — an organization cannot suspend itself.
          </p>
        </div>
        <div className="flex gap-2">
          {STATUS_OPTIONS.map((status) => (
            <button
              key={status}
              onClick={() => handleStatusChange(status)}
              disabled={updateStatus.isPending || org.status === status}
              className={`rounded-lg border px-3.5 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${
                org.status === status
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              {status}
            </button>
          ))}
        </div>
      </section>

      <Link href="/organizations" className="text-sm text-slate-500 hover:text-slate-700">
        ← Back to organizations
      </Link>
    </main>
  );
}
