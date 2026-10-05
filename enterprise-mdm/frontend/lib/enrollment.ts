"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId } from "@/lib/organization-store";
import {
  EnrollmentToken,
  CreateEnrollmentTokenRequest,
  CreateEnrollmentTokenResponse,
} from "@/types/enrollment";

export const ENROLLMENT_KEYS = {
  tokens: (organizationId: string | null) => ["enrollment-tokens", organizationId] as const,
};

export function useEnrollmentTokens() {
  const organizationId = useActiveOrganizationId();
  return useQuery<EnrollmentToken[]>({
    queryKey: ENROLLMENT_KEYS.tokens(organizationId),
    queryFn: () => apiClient.get<EnrollmentToken[]>("/api/v1/enrollment/tokens"),
    enabled: !!organizationId,
  });
}

export function useCreateEnrollmentToken() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<CreateEnrollmentTokenResponse, Error, CreateEnrollmentTokenRequest>({
    mutationFn: (body) =>
      apiClient.post<CreateEnrollmentTokenResponse>("/api/v1/enrollment/tokens", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ENROLLMENT_KEYS.tokens(organizationId) });
    },
  });
}

export function useRevokeEnrollmentToken() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<EnrollmentToken, Error, string>({
    mutationFn: (tokenId) =>
      apiClient.delete<EnrollmentToken>(`/api/v1/enrollment/tokens/${tokenId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ENROLLMENT_KEYS.tokens(organizationId) });
    },
  });
}
