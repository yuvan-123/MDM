export type DeviceCommandType =
  | "LOCK"
  | "REBOOT"
  | "WIPE"
  | "REFRESH_INFO"
  | "POLICY_SYNC"
  | "UNENROLL";

export type DeviceCommandStatus =
  | "PENDING"
  | "SENT"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "EXPIRED";

export interface DeviceCommand {
  id: string;
  organization_id: string;
  device_id: string;
  command_type: DeviceCommandType;
  status: DeviceCommandStatus;
  payload: Record<string, unknown> | null;
  provider_name: string;
  provider_command_id: string | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  requested_by: string | null;
  cancelled_by: string | null;
  cancelled_at: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
  expires_at: string;
  created_at: string;
}

export interface DeviceCommandListResponse {
  items: DeviceCommand[];
  total: number;
}

export interface IssueLockCommandRequest {
  message?: string;
  idempotency_key?: string;
}

export interface IssueRebootCommandRequest {
  idempotency_key?: string;
}

export interface IssueRefreshInfoCommandRequest {
  idempotency_key?: string;
}

export interface IssuePolicySyncCommandRequest {
  idempotency_key?: string;
}

export interface IssueWipeCommandRequest {
  confirm: true;
  idempotency_key: string;
}

export interface IssueUnenrollCommandRequest {
  confirm: true;
  idempotency_key: string;
}

export interface DeviceCommandCapabilitiesResponse {
  provider_name: string;
  provider_available: boolean;
  supported_command_types: DeviceCommandType[];
}
