"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { Loader2, AlertCircle, Plus, Search, Trash2, Pencil, Check, X } from "lucide-react";
import {
  useProviderDeviceLinks,
  useCreateProviderDeviceLink,
  useUpdateProviderDeviceLink,
  useDeleteProviderDeviceLink,
} from "@/lib/provider-device-links";
import { useDevices } from "@/lib/devices";
import { ApiError } from "@/lib/api/client";
import { ProviderDeviceLink } from "@/types/provider-device-link";

const PROVIDER_NAMES = ["android", "apple", "mock"] as const;
const PAGE_SIZE = 25;

const LINK_SOURCE_BADGE: Record<string, string> = {
  MANUAL: "bg-slate-100 text-slate-600 border-slate-200",
  PROVIDER_ENROLLED: "bg-emerald-50 text-emerald-700 border-emerald-200",
};

function CreateLinkForm() {
  const createLink = useCreateProviderDeviceLink();
  const [deviceSearch, setDeviceSearch] = useState("");
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [selectedDeviceName, setSelectedDeviceName] = useState("");
  const [providerName, setProviderName] = useState<string>("android");
  const [providerDeviceId, setProviderDeviceId] = useState("");
  const [applePushToken, setApplePushToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data: searchResults } = useDevices({ search: deviceSearch, page_size: 5 });

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!selectedDeviceId || !providerDeviceId.trim()) return;
    try {
      await createLink.mutateAsync({
        device_id: selectedDeviceId,
        provider_name: providerName,
        provider_device_id: providerDeviceId.trim(),
        apple_push_token:
          providerName === "apple" && applePushToken.trim() ? applePushToken.trim() : undefined,
      });
      setSelectedDeviceId(null);
      setSelectedDeviceName("");
      setDeviceSearch("");
      setProviderDeviceId("");
      setApplePushToken("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create provider device link.");
    }
  };

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Create a link</h2>
      <form onSubmit={handleSubmit} className="mt-4 space-y-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Device</label>
          {selectedDeviceId ? (
            <div className="mt-1.5 flex items-center justify-between rounded-lg border border-slate-300 bg-slate-50 px-3.5 py-2 text-sm text-slate-900">
              <span>{selectedDeviceName}</span>
              <button
                type="button"
                onClick={() => {
                  setSelectedDeviceId(null);
                  setSelectedDeviceName("");
                }}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="relative mt-1.5">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={deviceSearch}
                onChange={(e) => setDeviceSearch(e.target.value)}
                placeholder="Search devices by name, serial, manufacturer…"
                className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
              />
              {deviceSearch && searchResults && searchResults.items.length > 0 && (
                <ul className="absolute z-10 mt-1 w-full divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-lg">
                  {searchResults.items.map((device) => (
                    <li key={device.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedDeviceId(device.id);
                          setSelectedDeviceName(device.display_name);
                          setDeviceSearch("");
                        }}
                        className="block w-full px-3 py-2 text-left text-sm text-slate-900 hover:bg-slate-50"
                      >
                        {device.display_name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-600">Provider</label>
            <select
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              {PROVIDER_NAMES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">
              Provider device ID {providerName === "apple" ? "(UDID)" : ""}
            </label>
            <input
              type="text"
              value={providerDeviceId}
              onChange={(e) => setProviderDeviceId(e.target.value)}
              placeholder={providerName === "apple" ? "Device UDID" : "Provider's device identifier"}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
        </div>

        {providerName === "apple" && (
          <div>
            <label className="block text-xs font-medium text-slate-600">
              APNs push token (optional)
            </label>
            <input
              type="text"
              value={applePushToken}
              onChange={(e) => setApplePushToken(e.target.value)}
              placeholder="Current push token, if already known"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
        )}

        <button
          type="submit"
          disabled={createLink.isPending || !selectedDeviceId || !providerDeviceId.trim()}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
        >
          {createLink.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          Create link
        </button>

        {error && (
          <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error}
          </p>
        )}
      </form>
    </section>
  );
}

function LinkRow({ link }: { link: ProviderDeviceLink }) {
  const updateLink = useUpdateProviderDeviceLink();
  const deleteLink = useDeleteProviderDeviceLink();
  const [editing, setEditing] = useState(false);
  const [providerDeviceId, setProviderDeviceId] = useState(link.provider_device_id);
  const [applePushToken, setApplePushToken] = useState(link.apple_push_token ?? "");
  const [rowError, setRowError] = useState<string | null>(null);

  const handleSave = async () => {
    setRowError(null);
    try {
      await updateLink.mutateAsync({
        linkId: link.id,
        body: {
          provider_device_id:
            providerDeviceId.trim() !== link.provider_device_id ? providerDeviceId.trim() : undefined,
          apple_push_token:
            link.provider_name === "apple" && applePushToken.trim() !== (link.apple_push_token ?? "")
              ? applePushToken.trim()
              : undefined,
        },
      });
      setEditing(false);
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update link.");
    }
  };

  const handleDelete = async () => {
    setRowError(null);
    try {
      await deleteLink.mutateAsync(link.id);
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to delete link.");
    }
  };

  return (
    <li className="px-6 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded-full border border-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
              {link.provider_name}
            </span>
            <span
              className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                LINK_SOURCE_BADGE[link.link_source] ?? "border-slate-200 text-slate-600"
              }`}
            >
              {link.link_source}
            </span>
            <Link
              href={`/devices/${link.device_id}`}
              className="text-sm font-medium text-slate-900 hover:underline"
            >
              View device →
            </Link>
          </div>

          {editing ? (
            <div className="mt-2 space-y-2">
              <input
                type="text"
                value={providerDeviceId}
                onChange={(e) => setProviderDeviceId(e.target.value)}
                className="block w-full max-w-sm rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
              />
              {link.provider_name === "apple" && (
                <input
                  type="text"
                  value={applePushToken}
                  onChange={(e) => setApplePushToken(e.target.value)}
                  placeholder="APNs push token"
                  className="block w-full max-w-sm rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
                />
              )}
            </div>
          ) : (
            <div className="mt-0.5 space-y-0.5 text-xs text-slate-400">
              <p className="font-mono">{link.provider_device_id}</p>
              {link.apple_push_token && (
                <p className="font-mono">push token: {link.apple_push_token}</p>
              )}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {editing ? (
            <>
              <button
                onClick={handleSave}
                disabled={updateLink.isPending}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {updateLink.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                Save
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setProviderDeviceId(link.provider_device_id);
                  setApplePushToken(link.apple_push_token ?? "");
                }}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setEditing(true)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Pencil className="h-3.5 w-3.5" />
                Edit
              </button>
              <button
                onClick={handleDelete}
                disabled={deleteLink.isPending}
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
              >
                {deleteLink.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Unlink
              </button>
            </>
          )}
        </div>
      </div>
      {rowError && (
        <p role="alert" className="mt-2 flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-3.5 w-3.5" />
          {rowError}
        </p>
      )}
    </li>
  );
}

export default function ProviderDeviceLinksPage() {
  const [providerName, setProviderName] = useState<string>("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isError, error } = useProviderDeviceLinks({
    providerName: providerName || undefined,
    search: search || undefined,
    offset,
    limit: PAGE_SIZE,
  });

  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Provider Device Links</h1>
        <p className="mt-1 text-sm text-slate-600">
          The explicit mapping between an Android/Apple device identifier and a device in this
          organization — the identity outbound commands and inbound provider events both resolve
          through. Unlinking here removes only the mapping, never the device itself. See{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/phase-10.md
          </code>
          .
        </p>
      </div>

      <CreateLinkForm />

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={providerName}
          onChange={(e) => {
            setProviderName(e.target.value);
            setOffset(0);
          }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        >
          <option value="">All providers</option>
          {PROVIDER_NAMES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
          placeholder="Search provider device ID…"
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        />
      </div>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading links…
          </div>
        ) : isError ? (
          <p className="flex items-center gap-2 p-6 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error instanceof ApiError ? error.message : "Failed to load provider device links."}
          </p>
        ) : !data || data.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-500">No provider device links match these filters.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.items.map((link) => (
              <LinkRow key={link.id} link={link} />
            ))}
          </ul>
        )}
      </section>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            Page {page} of {pageCount} · {total} total
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="rounded-lg border border-slate-300 px-3 py-1.5 font-medium disabled:opacity-40"
            >
              Previous
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
              className="rounded-lg border border-slate-300 px-3 py-1.5 font-medium disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
