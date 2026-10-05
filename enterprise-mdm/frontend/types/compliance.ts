export type ComplianceStatus = "COMPLIANT" | "NON_COMPLIANT" | "UNKNOWN";
export type RuleOutcome = "PASS" | "FAIL" | "UNKNOWN";

export interface RuleResult {
  code: string;
  outcome: RuleOutcome;
  message: string;
}

export interface DeviceCompliance {
  device_id: string;
  organization_id: string;
  status: ComplianceStatus;
  policy_id: string | null;
  policy_version_id: string | null;
  evaluated_at: string | null;
  details: RuleResult[];
}

export interface DeviceComplianceListResponse {
  items: DeviceCompliance[];
  total: number;
}

export interface ComplianceSummary {
  total_devices: number;
  compliant: number;
  non_compliant: number;
  unknown: number;
  not_evaluated: number;
}
