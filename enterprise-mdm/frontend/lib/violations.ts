"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import { PolicyViolation, PolicyViolationListResponse, ViolationStatus } from "@/types/violation";

export const VIOLATION_KEYS = {
  list: (
    organizationId: string | null,
    deviceId: string,
    policyId: string,
    status: string
  ) => ["policy-violations", organizationId, deviceId, policyId, status] as const,
  detail: (organizationId: string | null, violationId: string) =>
    ["policy-violation", organizationId, violationId] as const,
};

export interface UseViolationsFilters {
  deviceId?: string;
  policyId?: string;
  status?: ViolationStatus;
  offset?: number;
  limit?: number;
}

export function useViolations({
  deviceId,
  policyId,
  status,
  offset = 0,
  limit = 50,
}: UseViolationsFilters) {
  const organizationId = useActiveOrganizationId();
  return useQuery<PolicyViolationListResponse>({
    queryKey: VIOLATION_KEYS.list(
      organizationId,
      deviceId ?? "all",
      policyId ?? "all",
      status ?? "all"
    ),
    queryFn: () =>
      apiClient.get<PolicyViolationListResponse>("/api/v1/compliance/violations", {
        params: { device_id: deviceId, policy_id: policyId, status, offset, limit },
      }),
    enabled: !!organizationId,
  });
}

function useInvalidateViolations() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["policy-violations", organizationId] });
}

export function useRetryRemediation() {
  const invalidate = useInvalidateViolations();
  return useMutation<PolicyViolation, Error, string>({
    mutationFn: (violationId) =>
      apiClient.post<PolicyViolation>(`/api/v1/compliance/violations/${violationId}/retry`),
    onSuccess: () => invalidate(),
  });
}

export function useResolveViolation() {
  const invalidate = useInvalidateViolations();
  return useMutation<PolicyViolation, Error, string>({
    mutationFn: (violationId) =>
      apiClient.post<PolicyViolation>(`/api/v1/compliance/violations/${violationId}/resolve`),
    onSuccess: () => invalidate(),
  });
}

export function useSetRemediationDisabled() {
  const invalidate = useInvalidateViolations();
  return useMutation<PolicyViolation, Error, { violationId: string; disabled: boolean }>({
    mutationFn: ({ violationId, disabled }) =>
      apiClient.post<PolicyViolation>(
        `/api/v1/compliance/violations/${violationId}/${disabled ? "disable" : "enable"}-remediation`
      ),
    onSuccess: () => invalidate(),
  });
}
