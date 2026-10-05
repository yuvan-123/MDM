export interface DeviceGroup {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  device_count: number;
  created_at: string;
  updated_at: string;
}

export interface DeviceGroupListResponse {
  items: DeviceGroup[];
  total: number;
}

export interface CreateDeviceGroupRequest {
  name: string;
  description?: string;
}

export interface UpdateDeviceGroupRequest {
  name?: string;
  description?: string;
}
