"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import { BackgroundJob, BackgroundJobListResponse, JobStatus, JobType } from "@/types/job";

export const JOB_KEYS = {
  list: (
    organizationId: string | null,
    jobType: string,
    status: string,
    commandId: string,
    offset: number
  ) => ["jobs", organizationId, jobType, status, commandId, offset] as const,
};

export interface UseJobsFilters {
  jobType?: JobType;
  status?: JobStatus;
  commandId?: string;
  offset?: number;
  limit?: number;
}

// Any non-terminal job in view means something is still moving — poll while
// that's true, stop once every job on the current page has settled. Mirrors
// the same "poll only while something could still change" reasoning as
// lib/commands.ts's command-status polling.
const ACTIVE_STATUSES: JobStatus[] = ["PENDING", "CLAIMED"];

export function useJobs({ jobType, status, commandId, offset = 0, limit = 25 }: UseJobsFilters) {
  const organizationId = useActiveOrganizationId();
  return useQuery<BackgroundJobListResponse>({
    queryKey: JOB_KEYS.list(
      organizationId,
      jobType ?? "all",
      status ?? "all",
      commandId ?? "all",
      offset
    ),
    queryFn: () =>
      apiClient.get<BackgroundJobListResponse>("/api/v1/jobs", {
        params: { job_type: jobType, status, command_id: commandId, offset, limit },
      }),
    enabled: !!organizationId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.items.some((job) => ACTIVE_STATUSES.includes(job.status)) ? 3000 : false;
    },
  });
}

// Phase 10: only a FAILED job may be retried (backend enforces this too —
// see docs/architecture/phase-10.md §4); invalidates every jobs list so a
// retried row's new PENDING status shows up wherever it's currently listed.
export function useRetryJob() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<BackgroundJob, Error, string>({
    mutationFn: (jobId) => apiClient.post<BackgroundJob>(`/api/v1/jobs/${jobId}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs", organizationId] });
    },
  });
}
