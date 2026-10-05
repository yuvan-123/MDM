"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId } from "@/lib/organization-store";
import {
  DeviceGroup,
  DeviceGroupListResponse,
  CreateDeviceGroupRequest,
  UpdateDeviceGroupRequest,
} from "@/types/device-group";
import { Device } from "@/types/device";

export const DEVICE_GROUP_KEYS = {
  list: (organizationId: string | null) => ["device-groups", organizationId] as const,
  detail: (organizationId: string | null, groupId: string) =>
    ["device-group", organizationId, groupId] as const,
  devices: (organizationId: string | null, groupId: string) =>
    ["device-group", organizationId, groupId, "devices"] as const,
};

function invalidateGroupLists(
  queryClient: ReturnType<typeof useQueryClient>,
  organizationId: string | null
) {
  queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.list(organizationId) });
}

export function useDeviceGroups() {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceGroupListResponse>({
    queryKey: DEVICE_GROUP_KEYS.list(organizationId),
    queryFn: () => apiClient.get<DeviceGroupListResponse>("/api/v1/device-groups"),
    enabled: !!organizationId,
  });
}

export function useDeviceGroup(groupId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceGroup>({
    queryKey: groupId
      ? DEVICE_GROUP_KEYS.detail(organizationId, groupId)
      : ["device-group", organizationId, "none"],
    queryFn: () => apiClient.get<DeviceGroup>(`/api/v1/device-groups/${groupId}`),
    enabled: !!organizationId && !!groupId,
  });
}

export function useDeviceGroupDevices(groupId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<Device[]>({
    queryKey: groupId
      ? DEVICE_GROUP_KEYS.devices(organizationId, groupId)
      : ["device-group", organizationId, "none", "devices"],
    queryFn: () => apiClient.get<Device[]>(`/api/v1/device-groups/${groupId}/devices`),
    enabled: !!organizationId && !!groupId,
  });
}

export function useCreateDeviceGroup() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<DeviceGroup, Error, CreateDeviceGroupRequest>({
    mutationFn: (body) => apiClient.post<DeviceGroup>("/api/v1/device-groups", body),
    onSuccess: () => invalidateGroupLists(queryClient, organizationId),
  });
}

export function useUpdateDeviceGroup(groupId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<DeviceGroup, Error, UpdateDeviceGroupRequest>({
    mutationFn: (body) => apiClient.patch<DeviceGroup>(`/api/v1/device-groups/${groupId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.detail(organizationId, groupId) });
      invalidateGroupLists(queryClient, organizationId);
    },
  });
}

export function useDeleteDeviceGroup() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, string>({
    mutationFn: (groupId) => apiClient.delete<void>(`/api/v1/device-groups/${groupId}`),
    onSuccess: () => invalidateGroupLists(queryClient, organizationId),
  });
}

export function useAddDeviceToGroup(groupId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, string>({
    mutationFn: (deviceId) =>
      apiClient.post<void>(`/api/v1/device-groups/${groupId}/devices/${deviceId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.devices(organizationId, groupId) });
      queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.detail(organizationId, groupId) });
    },
  });
}

export function useRemoveDeviceFromGroup(groupId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, string>({
    mutationFn: (deviceId) =>
      apiClient.delete<void>(`/api/v1/device-groups/${groupId}/devices/${deviceId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.devices(organizationId, groupId) });
      queryClient.invalidateQueries({ queryKey: DEVICE_GROUP_KEYS.detail(organizationId, groupId) });
    },
  });
}
