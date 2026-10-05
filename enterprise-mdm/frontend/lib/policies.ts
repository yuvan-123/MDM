"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId } from "@/lib/organization-store";
import {
  Policy,
  PolicyListResponse,
  PolicyVersion,
  PolicyAssignment,
  CreatePolicyRequest,
  UpdatePolicyRequest,
  AssignPolicyRequest,
} from "@/types/policy";

export const POLICY_KEYS = {
  list: (organizationId: string | null) => ["policies", organizationId] as const,
  detail: (organizationId: string | null, policyId: string) =>
    ["policy", organizationId, policyId] as const,
  versions: (organizationId: string | null, policyId: string) =>
    ["policy", organizationId, policyId, "versions"] as const,
  assignments: (organizationId: string | null, policyId: string) =>
    ["policy", organizationId, policyId, "assignments"] as const,
};

function invalidatePolicyLists(
  queryClient: ReturnType<typeof useQueryClient>,
  organizationId: string | null
) {
  queryClient.invalidateQueries({ queryKey: POLICY_KEYS.list(organizationId) });
}

export function usePolicies() {
  const organizationId = useActiveOrganizationId();
  return useQuery<PolicyListResponse>({
    queryKey: POLICY_KEYS.list(organizationId),
    queryFn: () => apiClient.get<PolicyListResponse>("/api/v1/policies"),
    enabled: !!organizationId,
  });
}

export function usePolicy(policyId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<Policy>({
    queryKey: policyId
      ? POLICY_KEYS.detail(organizationId, policyId)
      : ["policy", organizationId, "none"],
    queryFn: () => apiClient.get<Policy>(`/api/v1/policies/${policyId}`),
    enabled: !!organizationId && !!policyId,
  });
}

export function usePolicyVersions(policyId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<PolicyVersion[]>({
    queryKey: policyId
      ? POLICY_KEYS.versions(organizationId, policyId)
      : ["policy", organizationId, "none", "versions"],
    queryFn: () => apiClient.get<PolicyVersion[]>(`/api/v1/policies/${policyId}/versions`),
    enabled: !!organizationId && !!policyId,
  });
}

export function usePolicyAssignments(policyId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<PolicyAssignment[]>({
    queryKey: policyId
      ? POLICY_KEYS.assignments(organizationId, policyId)
      : ["policy", organizationId, "none", "assignments"],
    queryFn: () => apiClient.get<PolicyAssignment[]>(`/api/v1/policies/${policyId}/assignments`),
    enabled: !!organizationId && !!policyId,
  });
}

export function useCreatePolicy() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<Policy, Error, CreatePolicyRequest>({
    mutationFn: (body) => apiClient.post<Policy>("/api/v1/policies", body),
    onSuccess: () => invalidatePolicyLists(queryClient, organizationId),
  });
}

export function useUpdatePolicy(policyId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<Policy, Error, UpdatePolicyRequest>({
    mutationFn: (body) => apiClient.patch<Policy>(`/api/v1/policies/${policyId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: POLICY_KEYS.detail(organizationId, policyId) });
      queryClient.invalidateQueries({ queryKey: POLICY_KEYS.versions(organizationId, policyId) });
      invalidatePolicyLists(queryClient, organizationId);
    },
  });
}

export function useDeletePolicy() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<Policy, Error, string>({
    mutationFn: (policyId) => apiClient.delete<Policy>(`/api/v1/policies/${policyId}`),
    onSuccess: () => invalidatePolicyLists(queryClient, organizationId),
  });
}

export function useAssignPolicy(policyId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<PolicyAssignment, Error, AssignPolicyRequest>({
    mutationFn: (body) => apiClient.post<PolicyAssignment>(`/api/v1/policies/${policyId}/assign`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: POLICY_KEYS.assignments(organizationId, policyId) });
    },
  });
}

export function useUnassignPolicy(policyId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, AssignPolicyRequest>({
    mutationFn: (body) => apiClient.post<void>(`/api/v1/policies/${policyId}/unassign`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: POLICY_KEYS.assignments(organizationId, policyId) });
    },
  });
}
