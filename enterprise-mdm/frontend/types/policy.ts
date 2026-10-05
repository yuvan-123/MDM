export type PolicyStatus = "DRAFT" | "ACTIVE" | "ARCHIVED";
export type PolicyScopeType = "ORGANIZATION" | "USER" | "DEVICE_GROUP" | "DEVICE";

export interface PasswordPolicyConfig {
  minimum_length?: number | null;
  require_complexity?: boolean | null;
  max_age_days?: number | null;
  max_failed_attempts?: number | null;
}

export interface DeviceSecurityPolicyConfig {
  require_encryption?: boolean | null;
  require_screen_lock?: boolean | null;
  minimum_os_version?: string | null;
  block_rooted_jailbroken?: boolean | null;
}

export interface ApplicationPolicyConfig {
  allowed_app_ids?: string[] | null;
  denied_app_ids?: string[] | null;
  block_unapproved_installs?: boolean | null;
}

export interface NetworkPolicyConfig {
  require_wifi?: boolean | null;
  allowed_ssids?: string[] | null;
  require_vpn?: boolean | null;
}

export interface CompliancePolicyConfig {
  minimum_client_version?: string | null;
  grace_period_days?: number | null;
}

// Phase 13: command_type is a closed, non-destructive allowlist enforced
// by the backend schema (app/policies/schemas.py::RemediationPolicyConfig)
// — WIPE/UNENROLL/REBOOT are not valid values at all, not merely
// discouraged. Mirror that allowlist here so the form can't even offer them.
export type RemediationCommandType = "REFRESH_INFO" | "POLICY_SYNC" | "LOCK";
export type RemediationMode = "NONE" | "NOTIFY" | "COMMAND";

export interface RemediationPolicyConfig {
  mode: RemediationMode;
  command_type?: RemediationCommandType | null;
  max_attempts: number;
}

export interface PolicyConfiguration {
  password?: PasswordPolicyConfig | null;
  device_security?: DeviceSecurityPolicyConfig | null;
  application?: ApplicationPolicyConfig | null;
  network?: NetworkPolicyConfig | null;
  compliance?: CompliancePolicyConfig | null;
  remediation?: RemediationPolicyConfig | null;
}

export interface PolicyVersion {
  id: string;
  policy_id: string;
  version: number;
  configuration: PolicyConfiguration;
  created_at: string;
  created_by: string | null;
}

export interface Policy {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  status: PolicyStatus;
  current_version: PolicyVersion | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface PolicyListResponse {
  items: Policy[];
  total: number;
}

export interface CreatePolicyRequest {
  name: string;
  description?: string;
  configuration: PolicyConfiguration;
}

export interface UpdatePolicyRequest {
  name?: string;
  description?: string;
  status?: PolicyStatus;
  configuration?: PolicyConfiguration;
}

export interface PolicyAssignment {
  id: string;
  organization_id: string;
  policy_id: string;
  scope_type: PolicyScopeType;
  scope_id: string;
  created_at: string;
  created_by: string | null;
}

export interface AssignPolicyRequest {
  scope_type: PolicyScopeType;
  scope_id: string;
}
