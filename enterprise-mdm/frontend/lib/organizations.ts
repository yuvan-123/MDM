"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { setActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import {
  Organization,
  Membership,
  Invitation,
  CreateOrganizationRequest,
  UpdateOrganizationRequest,
  UpdateOrganizationStatusRequest,
  InviteMemberRequest,
  UpdateMembershipStatusRequest,
  AssignOrgRoleRequest,
  AcceptInvitationRequest,
  SelectOrganizationRequest,
  OrganizationContext,
} from "@/types/organization";

/**
 * Query key convention: every org-scoped key includes the organization id
 * so switching organizations can never surface stale cached data from a
 * previous one (see docs/architecture/multi-tenancy.md, frontend cache
 * isolation section).
 */
export const ORG_KEYS = {
  list: ["organizations"] as const,
  detail: (organizationId: string) => ["organizations", organizationId] as const,
  members: (organizationId: string) => ["organizations", organizationId, "members"] as const,
  current: ["organizations", "current"] as const,
};

export function useOrganizations() {
  return useQuery<Organization[]>({
    queryKey: ORG_KEYS.list,
    queryFn: () => apiClient.get<Organization[]>("/api/v1/organizations"),
  });
}

export function useOrganization(organizationId: string | null) {
  return useQuery<Organization>({
    queryKey: organizationId ? ORG_KEYS.detail(organizationId) : ["organizations", "none"],
    queryFn: () => apiClient.get<Organization>(`/api/v1/organizations/${organizationId}`),
    enabled: !!organizationId,
  });
}

export function useCreateOrganization() {
  const queryClient = useQueryClient();
  return useMutation<Organization, Error, CreateOrganizationRequest>({
    mutationFn: (body) => apiClient.post<Organization>("/api/v1/organizations", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.list });
    },
  });
}

export function useUpdateOrganization(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<Organization, Error, UpdateOrganizationRequest>({
    mutationFn: (body) =>
      apiClient.patch<Organization>(`/api/v1/organizations/${organizationId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.detail(organizationId) });
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.list });
    },
  });
}

export function useUpdateOrganizationStatus(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<Organization, Error, UpdateOrganizationStatusRequest>({
    mutationFn: (body) =>
      apiClient.patch<Organization>(`/api/v1/organizations/${organizationId}/status`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.detail(organizationId) });
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.list });
    },
  });
}

export function useOrganizationMembers(organizationId: string | null) {
  return useQuery<Membership[]>({
    queryKey: organizationId ? ORG_KEYS.members(organizationId) : ["organizations", "none", "members"],
    queryFn: () =>
      apiClient.get<Membership[]>(`/api/v1/organizations/${organizationId}/members`),
    enabled: !!organizationId,
  });
}

export function useInviteMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<Invitation, Error, InviteMemberRequest>({
    mutationFn: (body) =>
      apiClient.post<Invitation>(`/api/v1/organizations/${organizationId}/members`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.members(organizationId) });
    },
  });
}

export function useUpdateMemberStatus(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<Membership, Error, { membershipId: string; body: UpdateMembershipStatusRequest }>({
    mutationFn: ({ membershipId, body }) =>
      apiClient.patch<Membership>(
        `/api/v1/organizations/${organizationId}/members/${membershipId}`,
        body
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.members(organizationId) });
    },
  });
}

export function useRemoveMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (membershipId) =>
      apiClient.delete<void>(`/api/v1/organizations/${organizationId}/members/${membershipId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.members(organizationId) });
    },
  });
}

export function useAssignMemberRole(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<Membership, Error, { membershipId: string; body: AssignOrgRoleRequest }>({
    mutationFn: ({ membershipId, body }) =>
      apiClient.post<Membership>(
        `/api/v1/organizations/${organizationId}/members/${membershipId}/roles`,
        body
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.members(organizationId) });
    },
  });
}

export function useRemoveMemberRole(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, { membershipId: string; roleId: string }>({
    mutationFn: ({ membershipId, roleId }) =>
      apiClient.delete<void>(
        `/api/v1/organizations/${organizationId}/members/${membershipId}/roles/${roleId}`
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ORG_KEYS.members(organizationId) });
    },
  });
}

export function useAcceptInvitation() {
  return useMutation<Membership, Error, AcceptInvitationRequest>({
    mutationFn: (body) =>
      apiClient.post<Membership>("/api/v1/organizations/invitations/accept", body),
  });
}

/**
 * Validates the caller's access to an organization and returns the
 * resolved context + effective permissions. On success, sets it as the
 * active organization (persisted, and used by every subsequent
 * X-Organization-ID header) and invalidates every org-scoped query so no
 * stale data from a previously selected organization can surface.
 */
export function useSelectOrganization() {
  const queryClient = useQueryClient();
  return useMutation<OrganizationContext, Error, SelectOrganizationRequest>({
    mutationFn: (body) =>
      apiClient.post<OrganizationContext>("/api/v1/auth/select-organization", body),
    onSuccess: (data) => {
      setActiveOrganizationId(data.organization.id);
      queryClient.setQueryData(ORG_KEYS.current, data);
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] === "organizations" });
    },
  });
}

export function useCurrentOrganizationContext() {
  return useQuery<OrganizationContext | null>({
    queryKey: ORG_KEYS.current,
    queryFn: async () => {
      const organizationId = getActiveOrganizationId();
      if (!organizationId) return null;
      return apiClient.post<OrganizationContext>("/api/v1/auth/select-organization", {
        organization_id: organizationId,
      });
    },
    retry: false,
  });
}
