"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { getActiveOrganizationId, useActiveOrganizationId } from "@/lib/organization-store";
import {
  Device,
  DeviceListResponse,
  DeviceFilters,
  CreateDeviceRequest,
  UpdateDeviceRequest,
  AssignDeviceRequest,
} from "@/types/device";
import { DeviceEnrollment } from "@/types/enrollment";

/**
 * Query key convention (see docs/architecture/multi-tenancy.md, frontend
 * cache isolation section): every org-scoped key includes the organization
 * id so switching organizations can never surface stale data from a
 * previously selected one.
 */
export const DEVICE_KEYS = {
  list: (organizationId: string | null, filters: DeviceFilters) =>
    ["devices", organizationId, filters] as const,
  detail: (organizationId: string | null, deviceId: string) =>
    ["device", organizationId, deviceId] as const,
};

function filtersToParams(filters: DeviceFilters): Record<string, string | number | boolean | undefined> {
  return {
    page: filters.page,
    page_size: filters.page_size,
    search: filters.search || undefined,
    status: filters.status,
    platform: filters.platform,
    ownership_type: filters.ownership_type,
    enrollment_status: filters.enrollment_status,
    assigned_user_id: filters.assigned_user_id,
    provider_name: filters.provider_name,
    sort: filters.sort,
    sort_dir: filters.sort_dir,
  };
}

export function useDevices(filters: DeviceFilters) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceListResponse>({
    queryKey: DEVICE_KEYS.list(organizationId, filters),
    queryFn: () =>
      apiClient.get<DeviceListResponse>("/api/v1/devices", { params: filtersToParams(filters) }),
    enabled: !!organizationId,
  });
}

export function useDevice(deviceId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<Device>({
    queryKey: deviceId ? DEVICE_KEYS.detail(organizationId, deviceId) : ["device", organizationId, "none"],
    queryFn: () => apiClient.get<Device>(`/api/v1/devices/${deviceId}`),
    enabled: !!organizationId && !!deviceId,
  });
}

function invalidateDeviceLists(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] === "devices" });
}

export function useCreateDevice() {
  const queryClient = useQueryClient();
  return useMutation<Device, Error, CreateDeviceRequest>({
    mutationFn: (body) => apiClient.post<Device>("/api/v1/devices", body),
    onSuccess: () => invalidateDeviceLists(queryClient),
  });
}

export function useUpdateDevice(deviceId: string) {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<Device, Error, UpdateDeviceRequest>({
    mutationFn: (body) => apiClient.patch<Device>(`/api/v1/devices/${deviceId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICE_KEYS.detail(organizationId, deviceId) });
      invalidateDeviceLists(queryClient);
    },
  });
}

export function useDeleteDevice() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (deviceId) => apiClient.delete<void>(`/api/v1/devices/${deviceId}`),
    onSuccess: () => invalidateDeviceLists(queryClient),
  });
}

function useDeviceAction(action: "assign" | "unassign" | "block" | "unblock" | "retire") {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<Device, Error, { deviceId: string; body?: AssignDeviceRequest }>({
    mutationFn: ({ deviceId, body }) =>
      apiClient.post<Device>(`/api/v1/devices/${deviceId}/${action}`, body),
    onSuccess: (_data, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: DEVICE_KEYS.detail(organizationId, deviceId) });
      invalidateDeviceLists(queryClient);
    },
  });
}

export function useAssignDevice() {
  return useDeviceAction("assign");
}

export function useUnassignDevice() {
  return useDeviceAction("unassign");
}

export function useBlockDevice() {
  return useDeviceAction("block");
}

export function useUnblockDevice() {
  return useDeviceAction("unblock");
}

export function useRetireDevice() {
  return useDeviceAction("retire");
}

// ---------------------------------------------------------------------------
// Enrollment actions (Phase 5) — approve/reject/revoke a device's own
// enrollment. See app/enrollment/router.py's device_enrollment_router,
// mounted at this same "/devices" prefix.
// ---------------------------------------------------------------------------

function useDeviceEnrollmentAction(action: "approve" | "reject" | "revoke") {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<DeviceEnrollment, Error, { deviceId: string; reason?: string }>({
    mutationFn: ({ deviceId, reason }) =>
      apiClient.post(
        `/api/v1/devices/${deviceId}/enrollment/${action}`,
        action === "reject" ? { reason } : undefined
      ),
    onSuccess: (_data, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: DEVICE_KEYS.detail(organizationId, deviceId) });
      invalidateDeviceLists(queryClient);
    },
  });
}

export function useApproveEnrollment() {
  return useDeviceEnrollmentAction("approve");
}

export function useRejectEnrollment() {
  return useDeviceEnrollmentAction("reject");
}

export function useRevokeEnrollment() {
  return useDeviceEnrollmentAction("revoke");
}
