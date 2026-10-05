export type DevicePlatform = "ANDROID" | "IOS" | "IPADOS" | "OTHER";
export type DeviceType = "PHONE" | "TABLET" | "OTHER";
export type OwnershipType = "CORPORATE" | "BYOD" | "COPE" | "COBO";
export type DeviceStatus = "ACTIVE" | "INACTIVE" | "LOST" | "BLOCKED" | "RETIRED";
export type EnrollmentStatus =
  | "NOT_ENROLLED"
  | "PENDING"
  | "APPROVED"
  | "ENROLLED"
  | "REJECTED"
  | "FAILED"
  | "REVOKED"
  | "EXPIRED";
export type ConnectivityState = "ONLINE" | "STALE" | "OFFLINE" | "NEVER_CHECKED_IN";
export type ManagementState = "MANAGED" | "UNMANAGED";

export interface Device {
  id: string;
  organization_id: string;

  device_identifier: string | null;
  serial_number: string | null;
  display_name: string;

  manufacturer: string | null;
  model: string | null;
  model_identifier: string | null;

  platform: DevicePlatform;
  operating_system: string | null;
  os_version: string | null;
  security_patch_level: string | null;

  device_type: DeviceType;
  ownership_type: OwnershipType;

  status: DeviceStatus;
  enrollment_status: EnrollmentStatus;

  assigned_user_id: string | null;
  assigned_user_email: string | null;
  assigned_user_full_name: string | null;

  // Populated only when the caller holds device.view_sensitive.
  imei_last4: string | null;
  wifi_mac_last4: string | null;

  last_seen_at: string | null;
  last_check_in_at: string | null;
  enrolled_at: string | null;
  last_enrollment_attempt_at: string | null;
  connectivity_state: ConnectivityState;

  client_version: string | null;
  is_encrypted: boolean | null;
  has_screen_lock: boolean | null;
  is_rooted: boolean | null;

  // Phase 12: computed from ProviderDeviceLink existence, not stored.
  management_state: ManagementState;
  provider_name: string | null;
  provider_device_id: string | null;

  created_at: string;
  updated_at: string;
}

export interface DeviceListResponse {
  items: Device[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface CreateDeviceRequest {
  display_name: string;
  manufacturer?: string;
  model?: string;
  model_identifier?: string;
  platform: DevicePlatform;
  operating_system?: string;
  os_version?: string;
  device_type: DeviceType;
  ownership_type: OwnershipType;
  serial_number?: string;
  device_identifier?: string;
  imei?: string;
  wifi_mac?: string;
}

export interface UpdateDeviceRequest {
  display_name?: string;
  manufacturer?: string;
  model?: string;
  model_identifier?: string;
  operating_system?: string;
  os_version?: string;
  security_patch_level?: string;
  ownership_type?: OwnershipType;
  status?: DeviceStatus;
  serial_number?: string;
  device_identifier?: string;
}

export interface AssignDeviceRequest {
  user_id: string;
}

export interface DeviceFilters {
  page?: number;
  page_size?: number;
  search?: string;
  status?: DeviceStatus;
  platform?: DevicePlatform;
  ownership_type?: OwnershipType;
  enrollment_status?: EnrollmentStatus;
  assigned_user_id?: string;
  provider_name?: string;
  sort?: string;
  sort_dir?: "asc" | "desc";
}
