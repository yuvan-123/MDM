import { PolicyScopeType } from "@/types/policy";

export type DevicePlatform = "ANDROID" | "IOS" | "IPADOS" | "OTHER";
export type ApplicationRequirement = "REQUIRED" | "OPTIONAL";
export type ApplicationDeploymentStatus =
  | "PENDING"
  | "INSTALLING"
  | "INSTALLED"
  | "FAILED"
  | "REMOVED";

export interface Application {
  id: string;
  organization_id: string;
  name: string;
  platform: DevicePlatform;
  package_name: string | null;
  bundle_identifier: string | null;
  version: string | null;
  description: string | null;
  publisher: string | null;
  managed: boolean;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListResponse {
  items: Application[];
  total: number;
}

export interface CreateApplicationRequest {
  name: string;
  platform: DevicePlatform;
  package_name?: string | null;
  bundle_identifier?: string | null;
  version?: string | null;
  description?: string | null;
  publisher?: string | null;
  managed?: boolean;
}

export interface UpdateApplicationRequest {
  name?: string;
  version?: string | null;
  description?: string | null;
  publisher?: string | null;
  managed?: boolean;
}

export interface ApplicationAssignment {
  id: string;
  organization_id: string;
  application_id: string;
  scope_type: PolicyScopeType;
  scope_id: string;
  requirement: ApplicationRequirement;
  created_at: string;
  created_by: string | null;
}

export interface AssignApplicationRequest {
  scope_type: PolicyScopeType;
  scope_id: string;
  requirement: ApplicationRequirement;
}

export interface ApplicationDeployment {
  id: string;
  organization_id: string;
  device_id: string;
  application_id: string;
  assignment_id: string | null;
  requirement: ApplicationRequirement;
  status: ApplicationDeploymentStatus;
  attempts: number;
  last_command_id: string | null;
  last_error: string | null;
  installed_at: string | null;
  removed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationDeploymentListResponse {
  items: ApplicationDeployment[];
  total: number;
}

export interface DeployApplicationRequest {
  device_id?: string;
  device_ids?: string[];
  device_group_id?: string;
  organization_wide?: boolean;
}

export interface DeviceApplicationView {
  application: Application;
  requirement: ApplicationRequirement;
  deployment: ApplicationDeployment | null;
}

export interface DeviceApplicationsResponse {
  items: DeviceApplicationView[];
}
