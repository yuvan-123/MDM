"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { Users, Plus, Loader2, AlertCircle } from "lucide-react";
import { useDeviceGroups, useCreateDeviceGroup } from "@/lib/device-groups";
import { ApiError } from "@/lib/api/client";

export default function DeviceGroupsPage() {
  const { data, isLoading, isError } = useDeviceGroups();
  const createGroup = useCreateDeviceGroup();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return;
    try {
      await createGroup.mutateAsync({ name: name.trim(), description: description.trim() || undefined });
      setName("");
      setDescription("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create device group.");
    }
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Device Groups</h1>
        <p className="mt-1 text-sm text-slate-600">
          Group devices for policy assignment, bulk actions, and reporting.
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
              placeholder="Sales Team"
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
            disabled={createGroup.isPending || !name.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {createGroup.isPending ? (
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
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading groups…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load device groups.</p>
        ) : data && data.items.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {data.items.map((group) => (
              <li key={group.id}>
                <Link
                  href={`/device-groups/${group.id}`}
                  className="flex items-center justify-between px-6 py-4 hover:bg-slate-50"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                      <Users className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-900">{group.name}</p>
                      <p className="text-xs text-slate-400">{group.description || "No description"}</p>
                    </div>
                  </div>
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-slate-600">
                    {group.device_count} device{group.device_count === 1 ? "" : "s"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No device groups yet.</p>
        )}
      </section>
    </main>
  );
}
