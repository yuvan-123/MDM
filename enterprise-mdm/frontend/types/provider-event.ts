export type ProviderEventProcessingStatus = "PENDING" | "PROCESSED" | "FAILED" | "IGNORED";

export interface ProviderEvent {
  id: string;
  organization_id: string | null;
  device_id: string | null;
  provider_name: string;
  provider_event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  received_at: string;
  processing_status: ProviderEventProcessingStatus;
  processed_at: string | null;
  processing_error: string | null;
}

export interface ProviderEventListResponse {
  items: ProviderEvent[];
  total: number;
}
