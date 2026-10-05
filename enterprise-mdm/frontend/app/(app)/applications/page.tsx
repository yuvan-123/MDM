"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { AppWindow, Plus, Loader2, AlertCircle } from "lucide-react";
import { useApplications, useCreateApplication } from "@/lib/applications";
import { ApiError } from "@/lib/api/client";
import { DevicePlatform } from "@/types/application";

const PLATFORMS: DevicePlatform[] = ["ANDROID", "IOS", "IPADOS"];

const PLATFORM_BADGE: Record<DevicePlatform, string> = {
  ANDROID: "bg-emerald-50 text-emerald-700 border-emerald-200",
  IOS: "bg-blue-50 text-blue-700 border-blue-200",
  IPADOS: "bg-blue-50 text-blue-700 border-blue-200",
  OTHER: "bg-slate-100 text-slate-500 border-slate-200",
};

export default function ApplicationsPage() {
  const { data, isLoading, isError } = useApplications();
  const createApplication = useCreateApplication();
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState<DevicePlatform>("ANDROID");
  const [identifier, setIdentifier] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim() || !identifier.trim()) return;
    try {
      await createApplication.mutateAsync({
        name: name.trim(),
        platform,
        package_name: platform === "ANDROID" ? identifier.trim() : undefined,
        bundle_identifier: platform !== "ANDROID" ? identifier.trim() : undefined,
      });
      setName("");
      setIdentifier("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create application.");
    }
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Applications</h1>
        <p className="mt-1 text-sm text-slate-600">
          The application catalog — assign to a device, device group, or organization, and deploy
          through the same remote-command pipeline as lock/wipe/policy-sync.
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
              placeholder="Company Portal"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Platform</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value as DevicePlatform)}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">
              {platform === "ANDROID" ? "Package name" : "Bundle identifier"}
            </label>
            <input
              type="text"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder={platform === "ANDROID" ? "com.example.app" : "com.example.App"}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <button
            type="submit"
            disabled={createApplication.isPending || !name.trim() || !identifier.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {createApplication.isPending ? (
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
            Loading applications…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load applications.</p>
        ) : data && data.items.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {data.items.map((app) => (
              <li key={app.id}>
                <Link
                  href={`/applications/${app.id}`}
                  className="flex items-center justify-between px-6 py-4 hover:bg-slate-50"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                      <AppWindow className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-900">{app.name}</p>
                      <p className="text-xs text-slate-400">
                        {app.package_name || app.bundle_identifier} · {app.publisher || "No publisher"}
                      </p>
                    </div>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${PLATFORM_BADGE[app.platform]}`}
                  >
                    {app.platform}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No applications yet.</p>
        )}
      </section>
    </main>
  );
}
