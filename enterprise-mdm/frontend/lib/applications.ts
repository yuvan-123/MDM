"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId } from "@/lib/organization-store";
import {
  Application,
  ApplicationAssignment,
  ApplicationDeployment,
  ApplicationDeploymentListResponse,
  ApplicationListResponse,
  AssignApplicationRequest,
  CreateApplicationRequest,
  DeployApplicationRequest,
  DeviceApplicationsResponse,
  UpdateApplicationRequest,
} from "@/types/application";

export const APPLICATION_KEYS = {
  list: (organizationId: string | null) => ["applications", organizationId] as const,
  detail: (organizationId: string | null, applicationId: string) =>
    ["application", organizationId, applicationId] as const,
  assignments: (organizationId: string | null, applicationId: string) =>
    ["application", organizationId, applicationId, "assignments"] as const,
  deployments: (organizationId: string | null, applicationId: string) =>
    ["application", organizationId, applicationId, "deployments"] as const,
  deviceApplications: (organizationId: string | null, deviceId: string) =>
    ["device-applications", organizationId, deviceId] as const,
};

function invalidateApplicationLists(
  queryClient: ReturnType<typeof useQueryClient>,
  organizationId: string | null
) {
  queryClient.invalidateQueries({ queryKey: APPLICATION_KEYS.list(organizationId) });
}

export function useApplications() {
  const organizationId = useActiveOrganizationId();
  return useQuery<ApplicationListResponse>({
    queryKey: APPLICATION_KEYS.list(organizationId),
    queryFn: () => apiClient.get<ApplicationListResponse>("/api/v1/applications"),
    enabled: !!organizationId,
  });
}

export function useApplication(applicationId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<Application>({
    queryKey: applicationId
      ? APPLICATION_KEYS.detail(organizationId, applicationId)
      : ["application", organizationId, "none"],
    queryFn: () => apiClient.get<Application>(`/api/v1/applications/${applicationId}`),
    enabled: !!organizationId && !!applicationId,
  });
}

export function useApplicationAssignments(applicationId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<ApplicationAssignment[]>({
    queryKey: applicationId
      ? APPLICATION_KEYS.assignments(organizationId, applicationId)
      : ["application", organizationId, "none", "assignments"],
    queryFn: () =>
      apiClient.get<ApplicationAssignment[]>(`/api/v1/applications/${applicationId}/assignments`),
    enabled: !!organizationId && !!applicationId,
  });
}

export function useApplicationDeployments(applicationId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<ApplicationDeploymentListResponse>({
    queryKey: applicationId
      ? APPLICATION_KEYS.deployments(organizationId, applicationId)
      : ["application", organizationId, "none", "deployments"],
    queryFn: () =>
      apiClient.get<ApplicationDeploymentListResponse>(
        `/api/v1/applications/${applicationId}/deployments`
      ),
    enabled: !!organizationId && !!applicationId,
    refetchInterval: 5000,
  });
}

export function useDeviceApplications(deviceId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceApplicationsResponse>({
    queryKey: deviceId
      ? APPLICATION_KEYS.deviceApplications(organizationId, deviceId)
      : ["device-applications", organizationId, "none"],
    queryFn: () =>
      apiClient.get<DeviceApplicationsResponse>(`/api/v1/devices/${deviceId}/applications`),
    enabled: !!organizationId && !!deviceId,
  });
}

export function useCreateApplication() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<Application, Error, CreateApplicationRequest>({
    mutationFn: (body) => apiClient.post<Application>("/api/v1/applications", body),
    onSuccess: () => invalidateApplicationLists(queryClient, organizationId),
  });
}

export function useUpdateApplication(applicationId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<Application, Error, UpdateApplicationRequest>({
    mutationFn: (body) => apiClient.patch<Application>(`/api/v1/applications/${applicationId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: APPLICATION_KEYS.detail(organizationId, applicationId),
      });
      invalidateApplicationLists(queryClient, organizationId);
    },
  });
}

export function useDeleteApplication() {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, string>({
    mutationFn: (applicationId) => apiClient.delete<void>(`/api/v1/applications/${applicationId}`),
    onSuccess: () => invalidateApplicationLists(queryClient, organizationId),
  });
}

export function useAssignApplication(applicationId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<ApplicationAssignment, Error, AssignApplicationRequest>({
    mutationFn: (body) =>
      apiClient.post<ApplicationAssignment>(`/api/v1/applications/${applicationId}/assign`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: APPLICATION_KEYS.assignments(organizationId, applicationId),
      });
    },
  });
}

export function useUnassignApplication(applicationId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<void, Error, { scope_type: string; scope_id: string }>({
    mutationFn: (body) => apiClient.post<void>(`/api/v1/applications/${applicationId}/unassign`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: APPLICATION_KEYS.assignments(organizationId, applicationId),
      });
    },
  });
}

export function useDeployApplication(applicationId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<ApplicationDeploymentListResponse, Error, DeployApplicationRequest>({
    mutationFn: (body) =>
      apiClient.post<ApplicationDeploymentListResponse>(
        `/api/v1/applications/${applicationId}/deploy`,
        body
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: APPLICATION_KEYS.deployments(organizationId, applicationId),
      });
    },
  });
}

export function useRemoveApplicationFromDevice(applicationId: string) {
  const queryClient = useQueryClient();
  const organizationId = useActiveOrganizationId();
  return useMutation<ApplicationDeployment, Error, string>({
    mutationFn: (deviceId) =>
      apiClient.post<ApplicationDeployment>(
        `/api/v1/applications/${applicationId}/devices/${deviceId}/remove`
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: APPLICATION_KEYS.deployments(organizationId, applicationId),
      });
    },
  });
}
