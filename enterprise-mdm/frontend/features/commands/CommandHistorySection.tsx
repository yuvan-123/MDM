"use client";

import { Loader2, XCircle } from "lucide-react";
import { useDeviceCommands, useCancelCommand } from "@/lib/commands";
import { ApiError } from "@/lib/api/client";

const COMMAND_STATUS_BADGE: Record<string, string> = {
  PENDING: "bg-slate-100 text-slate-500 border-slate-200",
  SENT: "bg-blue-50 text-blue-700 border-blue-200",
  COMPLETED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  CANCELLED: "bg-slate-100 text-slate-500 border-slate-200",
  EXPIRED: "bg-slate-100 text-slate-400 border-slate-200",
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

interface CommandHistorySectionProps {
  deviceId: string;
  onError: (message: string) => void;
}

export function CommandHistorySection({ deviceId, onError }: CommandHistorySectionProps) {
  const { data, isLoading } = useDeviceCommands(deviceId);
  const cancelCommand = useCancelCommand();

  const handleCancel = async (commandId: string) => {
    try {
      await cancelCommand.mutateAsync({ deviceId, commandId });
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Cancel failed.");
    }
  };

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Command History</h2>
      {isLoading ? (
        <Loader2 className="mt-3 h-4 w-4 animate-spin text-slate-400" />
      ) : !data || data.items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">No commands issued yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100">
          {data.items.map((command) => (
            <li key={command.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">
                    {command.command_type.replace("_", " ")}
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                      COMMAND_STATUS_BADGE[command.status] ?? "border-slate-200 text-slate-600"
                    }`}
                  >
                    {command.status}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-slate-400">
                  {formatDate(command.created_at)}
                  {command.error_message ? ` · ${command.error_message}` : ""}
                </p>
              </div>
              {command.status === "SENT" && (
                <button
                  onClick={() => handleCancel(command.id)}
                  disabled={cancelCommand.isPending}
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <XCircle className="h-3.5 w-3.5" />
                  Cancel
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
