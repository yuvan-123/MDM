"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, AlertCircle, Trash2, Plus, Search } from "lucide-react";
import {
  useDeviceGroup,
  useDeviceGroupDevices,
  useAddDeviceToGroup,
  useRemoveDeviceFromGroup,
  useDeleteDeviceGroup,
} from "@/lib/device-groups";
import { useDevices } from "@/lib/devices";
import { ApiError } from "@/lib/api/client";

export default function DeviceGroupDetailPage() {
  const params = useParams<{ id: string }>();
  const groupId = params.id;
  const router = useRouter();

  const { data: group, isLoading, isError } = useDeviceGroup(groupId);
  const { data: members, isLoading: membersLoading } = useDeviceGroupDevices(groupId);
  const addDevice = useAddDeviceToGroup(groupId);
  const removeDevice = useRemoveDeviceFromGroup(groupId);
  const deleteGroup = useDeleteDeviceGroup();

  const [search, setSearch] = useState("");
  const { data: searchResults } = useDevices({ search, page_size: 5 });
  const [error, setError] = useState<string | null>(null);

  const memberIds = new Set((members ?? []).map((d) => d.id));

  const handleAdd = async (deviceId: string) => {
    setError(null);
    try {
      await addDevice.mutateAsync(deviceId);
      setSearch("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add device.");
    }
  };

  const handleDelete = async () => {
    try {
      await deleteGroup.mutateAsync(groupId);
      router.push("/device-groups");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete group.");
    }
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (isError || !group) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <p className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          Device group not found.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <div>
        <Link href="/device-groups" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to device groups
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{group.name}</h1>
            <p className="mt-1 text-sm text-slate-600">{group.description || "No description"}</p>
          </div>
          <button
            onClick={handleDelete}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100"
          >
            <Trash2 className="h-4 w-4" />
            Delete group
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
        <h2 className="text-sm font-semibold text-slate-900">Add a device</h2>
        <div className="relative mt-3">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search devices by name, serial, manufacturer…"
            className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        {search && searchResults && searchResults.items.length > 0 && (
          <ul className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200">
            {searchResults.items.map((device) => (
              <li key={device.id} className="flex items-center justify-between px-3 py-2">
                <span className="text-sm text-slate-900">{device.display_name}</span>
                {memberIds.has(device.id) ? (
                  <span className="text-xs text-slate-400">Already in group</span>
                ) : (
                  <button
                    onClick={() => handleAdd(device.id)}
                    disabled={addDevice.isPending}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    <Plus className="h-3 w-3" />
                    Add
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <h2 className="px-6 pt-6 text-sm font-semibold text-slate-900">
          Devices ({group.device_count})
        </h2>
        {membersLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading devices…
          </div>
        ) : members && members.length > 0 ? (
          <ul className="mt-2 divide-y divide-slate-100">
            {members.map((device) => (
              <li key={device.id} className="flex items-center justify-between px-6 py-3">
                <Link
                  href={`/devices/${device.id}`}
                  className="text-sm font-medium text-slate-900 hover:underline"
                >
                  {device.display_name}
                </Link>
                <button
                  onClick={() => removeDevice.mutate(device.id)}
                  disabled={removeDevice.isPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No devices in this group yet.</p>
        )}
      </section>
    </main>
  );
}
