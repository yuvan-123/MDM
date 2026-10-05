export type ViolationStatus =
  | "OPEN"
  | "REMEDIATION_PENDING"
  | "REMEDIATING"
  | "RESOLVED"
  | "REMEDIATION_FAILED";

export interface PolicyViolation {
  id: string;
  organization_id: string;
  device_id: string;
  policy_id: string;
  policy_version_id: string | null;
  rule_code: string;
  status: ViolationStatus;
  expected_value: string | null;
  current_value: string | null;
  message: string;
  detected_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolution_reason: string | null;
  remediation_attempts: number;
  last_remediation_command_id: string | null;
  last_remediation_at: string | null;
  automated_remediation_disabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PolicyViolationListResponse {
  items: PolicyViolation[];
  total: number;
}
