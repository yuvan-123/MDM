"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import {
  ProviderEvent,
  ProviderEventListResponse,
  ProviderEventProcessingStatus,
} from "@/types/provider-event";

export const PROVIDER_EVENT_KEYS = {
  list: (
    organizationId: string | null,
    providerName: string,
    eventType: string,
    status: string,
    offset: number
  ) => ["provider-events", organizationId, providerName, eventType, status, offset] as const,
};

export interface UseProviderEventsFilters {
  providerName?: string;
  eventType?: string;
  status?: ProviderEventProcessingStatus;
  offset?: number;
  limit?: number;
}

export function useProviderEvents({
  providerName,
  eventType,
  status,
  offset = 0,
  limit = 25,
}: UseProviderEventsFilters) {
  const organizationId = useActiveOrganizationId();
  return useQuery<ProviderEventListResponse>({
    queryKey: PROVIDER_EVENT_KEYS.list(
      organizationId,
      providerName ?? "all",
      eventType ?? "all",
      status ?? "all",
      offset
    ),
    queryFn: () =>
      apiClient.get<ProviderEventListResponse>("/api/v1/provider-events", {
        params: { provider_name: providerName, event_type: eventType, status, offset, limit },
      }),
    enabled: !!organizationId,
  });
}

// Phase 10: only a FAILED event may be retried — retry re-runs processing
// directly (see docs/architecture/phase-10.md §6), never re-triggers
// ingestion/redelivery-idempotency handling.
export function useRetryProviderEvent() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<ProviderEvent, Error, string>({
    mutationFn: (eventId) =>
      apiClient.post<ProviderEvent>(`/api/v1/provider-events/${eventId}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["provider-events", organizationId] });
    },
  });
}
