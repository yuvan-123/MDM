"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { useActiveOrganizationId, getActiveOrganizationId } from "@/lib/organization-store";
import { DEVICE_KEYS } from "@/lib/devices";
import {
  DeviceCommand,
  DeviceCommandCapabilitiesResponse,
  DeviceCommandListResponse,
  IssueLockCommandRequest,
  IssueRebootCommandRequest,
  IssueRefreshInfoCommandRequest,
  IssuePolicySyncCommandRequest,
  IssueUnenrollCommandRequest,
  IssueWipeCommandRequest,
} from "@/types/command";

export const COMMAND_KEYS = {
  list: (organizationId: string | null, deviceId: string) =>
    ["device-commands", organizationId, deviceId] as const,
  capabilities: (organizationId: string | null, deviceId: string) =>
    ["device-command-capabilities", organizationId, deviceId] as const,
};

// Phase 12: lets the UI show only commands the device's provider actually
// supports, instead of showing every button and letting unsupported ones
// fail on click. Capabilities don't change during a session (they're a
// function of platform + deployment-wide provider config, not per-device
// state), so this is fetched once per device, not polled.
export function useDeviceCommandCapabilities(deviceId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceCommandCapabilitiesResponse>({
    queryKey: deviceId
      ? COMMAND_KEYS.capabilities(organizationId, deviceId)
      : ["device-command-capabilities", organizationId, "none"],
    queryFn: () =>
      apiClient.get<DeviceCommandCapabilitiesResponse>(
        `/api/v1/devices/${deviceId}/commands/capabilities`
      ),
    enabled: !!organizationId && !!deviceId,
    staleTime: 5 * 60 * 1000,
  });
}

// Phase 8: command dispatch happens off the issuing request (a background
// job, not inline) — a freshly-issued command comes back PENDING, not its
// final outcome. Poll while anything on the page hasn't reached a terminal
// state yet, so the UI catches up to COMPLETED/FAILED/SENT without the user
// having to manually refresh; stop polling once nothing is still in flight.
const NON_TERMINAL_STATUSES = ["PENDING", "SENT"];

export function useDeviceCommands(deviceId: string | null) {
  const organizationId = useActiveOrganizationId();
  return useQuery<DeviceCommandListResponse>({
    queryKey: deviceId
      ? COMMAND_KEYS.list(organizationId, deviceId)
      : ["device-commands", organizationId, "none"],
    queryFn: () =>
      apiClient.get<DeviceCommandListResponse>(`/api/v1/devices/${deviceId}/commands`),
    enabled: !!organizationId && !!deviceId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.items.some((c) => NON_TERMINAL_STATUSES.includes(c.status)) ? 2000 : false;
    },
  });
}

function useIssueCommand<TBody>(path: string) {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<DeviceCommand, Error, { deviceId: string; body: TBody }>({
    mutationFn: ({ deviceId, body }) =>
      apiClient.post<DeviceCommand>(`/api/v1/devices/${deviceId}/commands/${path}`, body),
    onSuccess: (_data, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: COMMAND_KEYS.list(organizationId, deviceId) });
      // A completed command can change the device record itself — most
      // visibly REFRESH_INFO (os_version/client_version) and WIPE (clears
      // the heartbeat credential) — so the device detail query must be
      // invalidated alongside the command list, the same way every other
      // device-mutating action in lib/devices.ts already does.
      queryClient.invalidateQueries({ queryKey: DEVICE_KEYS.detail(organizationId, deviceId) });
    },
  });
}

export function useIssueLockCommand() {
  return useIssueCommand<IssueLockCommandRequest>("lock");
}

export function useIssueRebootCommand() {
  return useIssueCommand<IssueRebootCommandRequest>("reboot");
}

export function useIssueRefreshInfoCommand() {
  return useIssueCommand<IssueRefreshInfoCommandRequest>("refresh");
}

export function useIssuePolicySyncCommand() {
  return useIssueCommand<IssuePolicySyncCommandRequest>("policy-sync");
}

export function useIssueWipeCommand() {
  return useIssueCommand<IssueWipeCommandRequest>("wipe");
}

export function useIssueUnenrollCommand() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<DeviceCommand, Error, { deviceId: string; body: IssueUnenrollCommandRequest }>({
    mutationFn: ({ deviceId, body }) =>
      apiClient.post<DeviceCommand>(`/api/v1/devices/${deviceId}/commands/unenroll`, body),
    onSuccess: (_data, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: COMMAND_KEYS.list(organizationId, deviceId) });
      // A completed unenroll changes enrollment_status/management_state on
      // the device itself (see app/commands/service.py's UNENROLL side
      // effect) — same invalidation every other mutating command already does.
      queryClient.invalidateQueries({ queryKey: DEVICE_KEYS.detail(organizationId, deviceId) });
    },
  });
}

export function useCancelCommand() {
  const queryClient = useQueryClient();
  const organizationId = getActiveOrganizationId();
  return useMutation<DeviceCommand, Error, { deviceId: string; commandId: string }>({
    mutationFn: ({ deviceId, commandId }) =>
      apiClient.post<DeviceCommand>(`/api/v1/devices/${deviceId}/commands/${commandId}/cancel`),
    onSuccess: (_data, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: COMMAND_KEYS.list(organizationId, deviceId) });
    },
  });
}
