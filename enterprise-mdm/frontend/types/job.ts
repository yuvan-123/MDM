export type JobType = "COMPLIANCE_EVALUATION" | "POLICY_REEVALUATION_FANOUT" | "COMMAND_DISPATCH";

export type JobStatus = "PENDING" | "CLAIMED" | "COMPLETED" | "FAILED";

export interface BackgroundJob {
  id: string;
  organization_id: string;
  job_type: JobType;
  status: JobStatus;
  payload: Record<string, unknown>;
  dedup_key: string | null;
  attempts: number;
  max_attempts: number;
  scheduled_at: string;
  claimed_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface BackgroundJobListResponse {
  items: BackgroundJob[];
  total: number;
}
