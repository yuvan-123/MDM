export type ProviderDeviceLinkSource = "MANUAL" | "PROVIDER_ENROLLED";

export interface ProviderDeviceLink {
  id: string;
  organization_id: string;
  device_id: string;
  provider_name: string;
  provider_device_id: string;
  apple_push_token: string | null;
  link_source: ProviderDeviceLinkSource;
  created_at: string;
  updated_at: string;
}

export interface ProviderDeviceLinkListResponse {
  items: ProviderDeviceLink[];
  total: number;
}

export interface CreateProviderDeviceLinkRequest {
  device_id: string;
  provider_name: string;
  provider_device_id: string;
  apple_push_token?: string;
}

export interface UpdateProviderDeviceLinkRequest {
  provider_device_id?: string;
  apple_push_token?: string;
}
