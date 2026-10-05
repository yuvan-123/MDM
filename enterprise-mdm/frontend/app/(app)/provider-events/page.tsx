"use client";

import { useState } from "react";
import { Loader2, AlertCircle, ChevronDown, ChevronUp, RotateCcw } from "lucide-react";
import { useProviderEvents, useRetryProviderEvent } from "@/lib/provider-events";
import { ApiError } from "@/lib/api/client";
import { ProviderEvent, ProviderEventProcessingStatus } from "@/types/provider-event";

const PROCESSING_STATUSES: ProviderEventProcessingStatus[] = [
  "PENDING",
  "PROCESSED",
  "FAILED",
  "IGNORED",
];
const PAGE_SIZE = 25;

const STATUS_BADGE: Record<ProviderEventProcessingStatus, string> = {
  PENDING: "bg-slate-100 text-slate-500 border-slate-200",
  PROCESSED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  IGNORED: "bg-amber-50 text-amber-700 border-amber-200",
};

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function EventRow({ event }: { event: ProviderEvent }) {
  const [expanded, setExpanded] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const retryEvent = useRetryProviderEvent();

  const handleRetry = async () => {
    setRetryError(null);
    try {
      await retryEvent.mutateAsync(event.id);
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Failed to retry event.");
    }
  };

  return (
    <li className="px-6 py-3">
      <div className="flex w-full items-center justify-between gap-3">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-900">{event.event_type}</span>
              <span className="rounded-full border border-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
                {event.provider_name}
              </span>
              <span
                className={`rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[event.processing_status]}`}
              >
                {event.processing_status}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              received {formatDate(event.received_at)}
              {event.processing_error ? ` · ${event.processing_error}` : ""}
            </p>
          </div>
          {expanded ? (
            <ChevronUp className="h-4 w-4 shrink-0 text-slate-400" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
          )}
        </button>
        {event.processing_status === "FAILED" && (
          <button
            onClick={handleRetry}
            disabled={retryEvent.isPending}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {retryEvent.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            Retry
          </button>
        )}
      </div>
      {retryError && (
        <p role="alert" className="mt-2 flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-3.5 w-3.5" />
          {retryError}
        </p>
      )}
      {expanded && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-lg bg-slate-50 p-3 text-xs">
          <dt className="text-slate-500">Event ID</dt>
          <dd className="font-mono text-slate-700">{event.id}</dd>
          <dt className="text-slate-500">Provider event ID</dt>
          <dd className="font-mono text-slate-700">{event.provider_event_id}</dd>
          <dt className="text-slate-500">Device</dt>
          <dd className="font-mono text-slate-700">{event.device_id ?? "unresolved"}</dd>
          <dt className="text-slate-500">Processed at</dt>
          <dd className="text-slate-700">{formatDate(event.processed_at)}</dd>
          <dt className="col-span-2 text-slate-500">Payload</dt>
          <dd className="col-span-2 overflow-x-auto rounded bg-white p-2 font-mono text-slate-700">
            {JSON.stringify(event.payload)}
          </dd>
        </dl>
      )}
    </li>
  );
}

export default function ProviderEventsPage() {
  const [status, setStatus] = useState<ProviderEventProcessingStatus | "">("");
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isError, error } = useProviderEvents({
    status: status || undefined,
    offset,
    limit: PAGE_SIZE,
  });

  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Provider Events</h1>
        <p className="mt-1 text-sm text-slate-600">
          Inbound events from platform providers (Android/Apple), and how this organization
          processed them. No real provider traffic exists yet — see{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">
            docs/architecture/provider-events.md
          </code>
          .
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as ProviderEventProcessingStatus | "");
            setOffset(0);
          }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        >
          <option value="">All statuses</option>
          {PROCESSING_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading events…
          </div>
        ) : isError ? (
          <p className="flex items-center gap-2 p-6 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error instanceof ApiError ? error.message : "Failed to load provider events."}
          </p>
        ) : !data || data.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-500">No provider events match these filters.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.items.map((event) => (
              <EventRow key={event.id} event={event} />
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
