"use client";

import { useState } from "react";
import { Loader2, AlertCircle, ChevronDown, ChevronUp, RotateCcw } from "lucide-react";
import { useJobs, useRetryJob } from "@/lib/jobs";
import { ApiError } from "@/lib/api/client";
import { BackgroundJob, JobStatus, JobType } from "@/types/job";

const JOB_TYPES: JobType[] = ["COMPLIANCE_EVALUATION", "POLICY_REEVALUATION_FANOUT", "COMMAND_DISPATCH"];
const JOB_STATUSES: JobStatus[] = ["PENDING", "CLAIMED", "COMPLETED", "FAILED"];
const PAGE_SIZE = 25;

const STATUS_BADGE: Record<JobStatus, string> = {
  PENDING: "bg-slate-100 text-slate-500 border-slate-200",
  CLAIMED: "bg-blue-50 text-blue-700 border-blue-200",
  COMPLETED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
};

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function JobRow({ job }: { job: BackgroundJob }) {
  const [expanded, setExpanded] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const retryJob = useRetryJob();
  const isDeadLetter = job.status === "FAILED" && job.attempts >= job.max_attempts;

  const handleRetry = async () => {
    setRetryError(null);
    try {
      await retryJob.mutateAsync(job.id);
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Failed to retry job.");
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
              <span className="text-sm font-medium text-slate-900">
                {job.job_type.replace(/_/g, " ")}
              </span>
              <span
                className={`rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[job.status]}`}
              >
                {job.status}
              </span>
              {isDeadLetter && (
                <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700">
                  dead letter
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              attempts {job.attempts}/{job.max_attempts} · scheduled {formatDate(job.scheduled_at)}
              {job.last_error ? ` · ${job.last_error}` : ""}
            </p>
          </div>
          {expanded ? (
            <ChevronUp className="h-4 w-4 shrink-0 text-slate-400" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
          )}
        </button>
        {job.status === "FAILED" && (
          <button
            onClick={handleRetry}
            disabled={retryJob.isPending}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {retryJob.isPending ? (
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
          <dt className="text-slate-500">Job ID</dt>
          <dd className="font-mono text-slate-700">{job.id}</dd>
          <dt className="text-slate-500">Claimed at</dt>
          <dd className="text-slate-700">{formatDate(job.claimed_at)}</dd>
          <dt className="text-slate-500">Completed at</dt>
          <dd className="text-slate-700">{formatDate(job.completed_at)}</dd>
          <dt className="text-slate-500">Created at</dt>
          <dd className="text-slate-700">{formatDate(job.created_at)}</dd>
          <dt className="col-span-2 text-slate-500">Payload</dt>
          <dd className="col-span-2 overflow-x-auto rounded bg-white p-2 font-mono text-slate-700">
            {JSON.stringify(job.payload)}
          </dd>
        </dl>
      )}
    </li>
  );
}

export default function JobsPage() {
  const [jobType, setJobType] = useState<JobType | "">("");
  const [status, setStatus] = useState<JobStatus | "">("");
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isError, error } = useJobs({
    jobType: jobType || undefined,
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
        <h1 className="text-2xl font-bold text-slate-900">Background Jobs</h1>
        <p className="mt-1 text-sm text-slate-600">
          Compliance evaluations, policy re-evaluation fanout, and command dispatch — the
          asynchronous work this organization&apos;s worker processes off the request path.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={jobType}
          onChange={(e) => {
            setJobType(e.target.value as JobType | "");
            setOffset(0);
          }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        >
          <option value="">All job types</option>
          {JOB_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as JobStatus | "");
            setOffset(0);
          }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        >
          <option value="">All statuses</option>
          {JOB_STATUSES.map((s) => (
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
            Loading jobs…
          </div>
        ) : isError ? (
          <p className="flex items-center gap-2 p-6 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error instanceof ApiError ? error.message : "Failed to load jobs."}
          </p>
        ) : !data || data.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-500">No jobs match these filters.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.items.map((job) => (
              <JobRow key={job.id} job={job} />
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
