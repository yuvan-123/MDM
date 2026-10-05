"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { ShieldCheck, Plus, Loader2, AlertCircle } from "lucide-react";
import { usePolicies, useCreatePolicy } from "@/lib/policies";
import { ApiError } from "@/lib/api/client";
import { PolicyStatus } from "@/types/policy";

const STATUS_BADGE: Record<PolicyStatus, string> = {
  DRAFT: "bg-slate-100 text-slate-600 border-slate-200",
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  ARCHIVED: "bg-slate-100 text-slate-400 border-slate-200",
};

export default function PoliciesPage() {
  const { data, isLoading, isError } = usePolicies();
  const createPolicy = useCreatePolicy();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return;
    try {
      await createPolicy.mutateAsync({
        name: name.trim(),
        description: description.trim() || undefined,
        configuration: {},
      });
      setName("");
      setDescription("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create policy.");
    }
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Policies</h1>
        <p className="mt-1 text-sm text-slate-600">
          Versioned device policies — assign to an organization, a device group, a user, or a device.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <form onSubmit={handleCreate} className="flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Baseline Security Policy"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <button
            type="submit"
            disabled={createPolicy.isPending || !name.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {createPolicy.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Create
          </button>
        </form>
        {error && (
          <p role="alert" className="mt-2 flex items-center gap-1.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error}
          </p>
        )}
        <p className="mt-2 text-xs text-slate-400">
          New policies start as DRAFT with an empty configuration — edit and activate from the detail page.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading policies…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load policies.</p>
        ) : data && data.items.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {data.items.map((policy) => (
              <li key={policy.id}>
                <Link
                  href={`/policies/${policy.id}`}
                  className="flex items-center justify-between px-6 py-4 hover:bg-slate-50"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                      <ShieldCheck className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-900">{policy.name}</p>
                      <p className="text-xs text-slate-400">
                        v{policy.current_version?.version ?? "—"} · {policy.description || "No description"}
                      </p>
                    </div>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE[policy.status]}`}
                  >
                    {policy.status}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No policies yet.</p>
        )}
      </section>
    </main>
  );
}
