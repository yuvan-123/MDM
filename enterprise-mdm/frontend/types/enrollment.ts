import { DevicePlatform, DeviceType, Device } from "./device";

export type EnrollmentTokenStatus = "ACTIVE" | "REVOKED" | "EXPIRED" | "EXHAUSTED";
export type DeviceEnrollmentStatus =
  | "PENDING"
  | "APPROVED"
  | "ENROLLED"
  | "REJECTED"
  | "REVOKED"
  | "EXPIRED";

export interface EnrollmentToken {
  id: string;
  organization_id: string;
  label: string | null;
  status: EnrollmentTokenStatus;
  require_approval: boolean;
  max_uses: number;
  use_count: number;
  expires_at: string;
  revoked_at: string | null;
  target_user_id: string | null;
  allowed_platform: DevicePlatform | null;
  allowed_device_type: DeviceType | null;
  provision_via_provider: boolean;
  provider_enrollment_reference: string | null;
  provider_enrollment_expires_at: string | null;
  created_at: string;
}

export interface CreateEnrollmentTokenResponse extends EnrollmentToken {
  token: string;
  provider_enrollment_value: string | null;
  provider_enrollment_qr_code: string | null;
}

export interface CreateEnrollmentTokenRequest {
  label?: string;
  expires_in_hours: number;
  require_approval?: boolean;
  max_uses?: number;
  target_user_id?: string;
  allowed_platform?: DevicePlatform;
  allowed_device_type?: DeviceType;
  provision_via_provider?: boolean;
}

export interface DeviceEnrollment {
  id: string;
  organization_id: string;
  device_id: string;
  enrollment_token_id: string | null;
  status: DeviceEnrollmentStatus;
  expires_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  created_at: string;
  provider_name: string | null;
  provider_device_id: string | null;
}

export interface RegisterDeviceResponse {
  device: Device;
  device_id: string;
  device_secret: string;
  enrollment_id: string;
  enrollment_status: DeviceEnrollmentStatus;
  provider_link_created: boolean;
}
