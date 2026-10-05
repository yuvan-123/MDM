"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { Smartphone, Plus, Loader2, AlertCircle, Search } from "lucide-react";
import { useDevices, useCreateDevice } from "@/lib/devices";
import { ApiError } from "@/lib/api/client";
import { DeviceFilters, DevicePlatform, DeviceStatus, DeviceType, OwnershipType } from "@/types/device";

const STATUS_BADGE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  INACTIVE: "bg-slate-100 text-slate-500 border-slate-200",
  LOST: "bg-amber-50 text-amber-700 border-amber-200",
  BLOCKED: "bg-red-50 text-red-700 border-red-200",
  RETIRED: "bg-slate-100 text-slate-400 border-slate-200",
};

const PLATFORMS: DevicePlatform[] = ["ANDROID", "IOS", "IPADOS", "OTHER"];
const STATUSES: DeviceStatus[] = ["ACTIVE", "INACTIVE", "LOST", "BLOCKED", "RETIRED"];
const OWNERSHIP_TYPES: OwnershipType[] = ["CORPORATE", "BYOD", "COPE", "COBO"];
const DEVICE_TYPES: DeviceType[] = ["PHONE", "TABLET", "OTHER"];

function timeAgo(iso: string | null): string {
  if (!iso) return "Never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function CreateDeviceForm({ onCreated }: { onCreated: () => void }) {
  const createDevice = useCreateDevice();
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [model, setModel] = useState("");
  const [platform, setPlatform] = useState<DevicePlatform>("ANDROID");
  const [deviceType, setDeviceType] = useState<DeviceType>("PHONE");
  const [ownershipType, setOwnershipType] = useState<OwnershipType>("CORPORATE");
  const [serialNumber, setSerialNumber] = useState("");
  const [deviceIdentifier, setDeviceIdentifier] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!displayName.trim()) return;
    try {
      await createDevice.mutateAsync({
        display_name: displayName.trim(),
        manufacturer: manufacturer.trim() || undefined,
        model: model.trim() || undefined,
        platform,
        device_type: deviceType,
        ownership_type: ownershipType,
        serial_number: serialNumber.trim() || undefined,
        device_identifier: deviceIdentifier.trim() || undefined,
      });
      setDisplayName("");
      setManufacturer("");
      setModel("");
      setSerialNumber("");
      setDeviceIdentifier("");
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create device.");
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800"
      >
        <Plus className="h-4 w-4" />
        Add device
      </button>
    );
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">Add a device to inventory</h2>
      <p className="mt-1 text-xs text-slate-400">
        Organization is set automatically. This creates an inventory record only — it does not
        enroll or contact a physical device.
      </p>
      <form onSubmit={handleSubmit} className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Display name *</label>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="John's Galaxy S24"
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Manufacturer</label>
          <input
            value={manufacturer}
            onChange={(e) => setManufacturer(e.target.value)}
            placeholder="Samsung"
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Model</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Galaxy S24"
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Platform</label>
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value as DevicePlatform)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Device type</label>
          <select
            value={deviceType}
            onChange={(e) => setDeviceType(e.target.value as DeviceType)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            {DEVICE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Ownership</label>
          <select
            value={ownershipType}
            onChange={(e) => setOwnershipType(e.target.value as OwnershipType)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            {OWNERSHIP_TYPES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Serial number</label>
          <input
            value={serialNumber}
            onChange={(e) => setSerialNumber(e.target.value)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Device identifier</label>
          <input
            value={deviceIdentifier}
            onChange={(e) => setDeviceIdentifier(e.target.value)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div className="col-span-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={createDevice.isPending || !displayName.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {createDevice.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Create
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
        </div>
      </form>
      {error && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {error}
        </p>
      )}
    </section>
  );
}

export default function DevicesPage() {
  const [filters, setFilters] = useState<DeviceFilters>({ page: 1, page_size: 25 });
  const [searchInput, setSearchInput] = useState("");
  const { data, isLoading, isError, refetch } = useDevices(filters);

  const updateFilter = <K extends keyof DeviceFilters>(key: K, value: DeviceFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value, page: 1 }));
  };

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault();
    updateFilter("search", searchInput.trim() || undefined);
  };

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Devices</h1>
          <p className="mt-1 text-sm text-slate-600">
            Inventory records for this organization. Creating a record does not enroll or manage a
            physical device.
          </p>
        </div>
      </div>

      <CreateDeviceForm onCreated={() => refetch()} />

      <section className="flex flex-wrap items-end gap-3">
        <form onSubmit={handleSearchSubmit} className="flex items-end gap-2">
          <div>
            <label className="block text-xs font-medium text-slate-600">Search</label>
            <div className="relative mt-1.5">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search devices…"
                className="rounded-lg border border-slate-300 py-2 pl-8 pr-3 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
              />
            </div>
          </div>
          <button
            type="submit"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Go
          </button>
        </form>

        <div>
          <label className="block text-xs font-medium text-slate-600">Platform</label>
          <select
            value={filters.platform ?? ""}
            onChange={(e) => updateFilter("platform", (e.target.value || undefined) as DevicePlatform | undefined)}
            className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            <option value="">All platforms</option>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">Status</label>
          <select
            value={filters.status ?? ""}
            onChange={(e) => updateFilter("status", (e.target.value || undefined) as DeviceStatus | undefined)}
            className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">Ownership</label>
          <select
            value={filters.ownership_type ?? ""}
            onChange={(e) =>
              updateFilter("ownership_type", (e.target.value || undefined) as OwnershipType | undefined)
            }
            className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            <option value="">All ownership</option>
            {OWNERSHIP_TYPES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">Managed by</label>
          <select
            value={filters.provider_name ?? ""}
            onChange={(e) => updateFilter("provider_name", e.target.value || undefined)}
            className="mt-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          >
            <option value="">Any provider</option>
            <option value="android">android</option>
            <option value="apple">apple</option>
            <option value="mock">mock</option>
          </select>
        </div>
      </section>

      <section className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading devices…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load devices.</p>
        ) : data && data.items.length > 0 ? (
          <>
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-6 py-3 font-medium">Device</th>
                  <th className="px-6 py-3 font-medium">Platform</th>
                  <th className="px-6 py-3 font-medium">Status</th>
                  <th className="px-6 py-3 font-medium">Managed</th>
                  <th className="px-6 py-3 font-medium">User</th>
                  <th className="px-6 py-3 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((device) => (
                  <tr key={device.id} className="hover:bg-slate-50">
                    <td className="px-6 py-3">
                      <Link
                        href={`/devices/${device.id}`}
                        className="flex items-center gap-2 font-medium text-slate-900 hover:underline"
                      >
                        <Smartphone className="h-4 w-4 text-slate-400" />
                        {device.display_name}
                      </Link>
                    </td>
                    <td className="px-6 py-3 text-slate-600">{device.platform}</td>
                    <td className="px-6 py-3">
                      <span
                        className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                          STATUS_BADGE[device.status] ?? "border-slate-200 text-slate-600"
                        }`}
                      >
                        {device.status}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      {device.management_state === "MANAGED" ? (
                        <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                          {device.provider_name}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">Unmanaged</span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-slate-600">
                      {device.assigned_user_full_name ?? <span className="text-slate-400">Unassigned</span>}
                    </td>
                    <td className="px-6 py-3 text-slate-500">{timeAgo(device.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="flex items-center justify-between border-t border-slate-100 px-6 py-3 text-sm text-slate-500">
              <span>
                Page {data.page} of {Math.max(data.total_pages, 1)} · {data.total} device
                {data.total === 1 ? "" : "s"}
              </span>
              <div className="flex gap-2">
                <button
                  disabled={data.page <= 1}
                  onClick={() => setFilters((prev) => ({ ...prev, page: (prev.page ?? 1) - 1 }))}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={data.page >= data.total_pages}
                  onClick={() => setFilters((prev) => ({ ...prev, page: (prev.page ?? 1) + 1 }))}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        ) : (
          <p className="p-6 text-sm text-slate-500">No devices yet. Add one above to get started.</p>
        )}
      </section>
    </main>
  );
}
