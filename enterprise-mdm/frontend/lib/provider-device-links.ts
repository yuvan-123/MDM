"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import {
  ProviderDeviceLink,
  ProviderDeviceLinkListResponse,
  CreateProviderDeviceLinkRequest,
  UpdateProviderDeviceLinkRequest,
} from "@/types/provider-device-link";

export const PROVIDER_DEVICE_LINK_KEYS = {
  list: (
    organizationId: string | null,
    providerName: string,
    deviceId: string,
    search: string,
    offset: number
  ) => ["provider-device-links", organizationId, providerName, deviceId, search, offset] as const,
};

export interface UseProviderDeviceLinksFilters {
  providerName?: string;
  deviceId?: string;
  search?: string;
  offset?: number;
  limit?: number;
}

export function useProviderDeviceLinks({
  providerName,
  deviceId,
  search,
  offset = 0,
  limit = 25,
}: UseProviderDeviceLinksFilters) {
  const organizationId = useActiveOrganizationId();
  return useQuery<ProviderDeviceLinkListResponse>({
    queryKey: PROVIDER_DEVICE_LINK_KEYS.list(
      organizationId,
      providerName ?? "all",
      deviceId ?? "all",
      search ?? "",
      offset
    ),
    queryFn: () =>
      apiClient.get<ProviderDeviceLinkListResponse>("/api/v1/provider-device-links", {
        params: { provider_name: providerName, device_id: deviceId, search, offset, limit },
      }),
    enabled: !!organizationId,
  });
}

function useInvalidateProviderDeviceLinks() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["provider-device-links", organizationId] });
}

export function useCreateProviderDeviceLink() {
  const invalidate = useInvalidateProviderDeviceLinks();
  return useMutation<ProviderDeviceLink, Error, CreateProviderDeviceLinkRequest>({
    mutationFn: (body) =>
      apiClient.post<ProviderDeviceLink>("/api/v1/provider-device-links", body),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateProviderDeviceLink() {
  const invalidate = useInvalidateProviderDeviceLinks();
  return useMutation<
    ProviderDeviceLink,
    Error,
    { linkId: string; body: UpdateProviderDeviceLinkRequest }
  >({
    mutationFn: ({ linkId, body }) =>
      apiClient.patch<ProviderDeviceLink>(`/api/v1/provider-device-links/${linkId}`, body),
    onSuccess: () => invalidate(),
  });
}

export function useDeleteProviderDeviceLink() {
  const invalidate = useInvalidateProviderDeviceLinks();
  return useMutation<void, Error, string>({
    mutationFn: (linkId) => apiClient.delete<void>(`/api/v1/provider-device-links/${linkId}`),
    onSuccess: () => invalidate(),
  });
}
