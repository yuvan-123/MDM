"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId } from "@/lib/organization-store";
import { DeviceCompliance, DeviceComplianceListResponse, ComplianceSummary } from "@/types/compliance";

export const COMPLIANCE_KEYS = {
  summary: (organizationId: string | null) => ["compliance-summary", organizationId] as const,
  nonCompliant: (organizationId: string | null) =>
    ["compliance-non-compliant", organizationId] as const,
  device: (organizationId: string | null, deviceId: string) =>
    ["compliance-device", organizationId, deviceId] as const,
};

export function useComplianceSummary() {
  const organizationId = useActiveOrganizationId();
  return useQuery<ComplianceSummary>({
    queryKey: COMPLIANCE_KEYS.summary(organizationId),
    queryFn: () => apiClient.get<ComplianceSummary>("/api/v1/compliance/summary"),
    enabled: !!organizationId,
  });
}

export function useNonCompliantDevices() {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceComplianceListResponse>({
    queryKey: COMPLIANCE_KEYS.nonCompliant(organizationId),
    queryFn: () => apiClient.get<DeviceComplianceListResponse>("/api/v1/compliance/non-compliant"),
    enabled: !!organizationId,
  });
}

export function useDeviceCompliance(deviceId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceCompliance>({
    queryKey: deviceId
      ? COMPLIANCE_KEYS.device(organizationId, deviceId)
      : ["compliance-device", organizationId, "none"],
    queryFn: () => apiClient.get<DeviceCompliance>(`/api/v1/compliance/devices/${deviceId}`),
    enabled: !!organizationId && !!deviceId,
  });
}

export function useEvaluateCompliance() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<DeviceCompliance, Error, string>({
    mutationFn: (deviceId) =>
      apiClient.post<DeviceCompliance>(`/api/v1/compliance/evaluate/${deviceId}`),
    onSuccess: (data, deviceId) => {
      queryClient.setQueryData(COMPLIANCE_KEYS.device(organizationId, deviceId), data);
      queryClient.invalidateQueries({ queryKey: COMPLIANCE_KEYS.summary(organizationId) });
      queryClient.invalidateQueries({ queryKey: COMPLIANCE_KEYS.nonCompliant(organizationId) });
    },
  });
}
