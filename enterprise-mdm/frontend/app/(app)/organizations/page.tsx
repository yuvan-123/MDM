"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { Building2, Plus, Loader2, AlertCircle } from "lucide-react";
import { useOrganizations, useCreateOrganization } from "@/lib/organizations";
import { ApiError } from "@/lib/api/client";

const STATUS_BADGE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  SUSPENDED: "bg-amber-50 text-amber-700 border-amber-200",
  DISABLED: "bg-red-50 text-red-700 border-red-200",
};

export default function OrganizationsPage() {
  const { data: organizations, isLoading, isError } = useOrganizations();
  const createOrganization = useCreateOrganization();
  const [newName, setNewName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!newName.trim()) return;
    try {
      await createOrganization.mutateAsync({ name: newName.trim() });
      setNewName("");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create organization.");
    }
  };

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Organizations</h1>
        <p className="mt-1 text-sm text-slate-600">
          Organizations you belong to. Platform administrators see every organization.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <form onSubmit={handleCreate} className="flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">
              New organization name
            </label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Acme Corporation"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <button
            type="submit"
            disabled={createOrganization.isPending || !newName.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 disabled:opacity-50"
          >
            {createOrganization.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Create
          </button>
        </form>
        {formError && (
          <p role="alert" className="mt-2 flex items-center gap-1.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {formError}
          </p>
        )}
        <p className="mt-2 text-xs text-slate-400">
          Only platform administrators can create organizations.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading organizations…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load organizations.</p>
        ) : organizations && organizations.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {organizations.map((org) => (
              <li key={org.id}>
                <Link
                  href={`/organizations/${org.id}`}
                  className="flex items-center justify-between px-6 py-4 hover:bg-slate-50"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                      <Building2 className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-900">{org.name}</p>
                      <p className="text-xs text-slate-400">{org.slug}</p>
                    </div>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                      STATUS_BADGE[org.status] ?? "border-slate-200 text-slate-600"
                    }`}
                  >
                    {org.status}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No organizations yet.</p>
        )}
      </section>
    </main>
  );
}
