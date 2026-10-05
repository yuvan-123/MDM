export type OrganizationStatus = "ACTIVE" | "SUSPENDED" | "DISABLED";
export type MembershipStatus = "ACTIVE" | "INVITED" | "SUSPENDED" | "REMOVED";
export type InvitationStatus = "PENDING" | "ACCEPTED" | "EXPIRED" | "REVOKED";

export interface Organization {
  id: string;
  name: string;
  slug: string;
  status: OrganizationStatus;
  created_at: string;
  updated_at: string;
}

export interface MembershipRole {
  id: string;
  role_name: string;
  granted_at: string;
}

export interface Membership {
  id: string;
  organization_id: string;
  user_id: string;
  user_email: string;
  user_full_name: string;
  status: MembershipStatus;
  created_at: string;
  roles: MembershipRole[];
}

export interface Invitation {
  id: string;
  organization_id: string;
  email: string;
  status: InvitationStatus;
  created_at: string;
  expires_at: string;
}

export interface CreateOrganizationRequest {
  name: string;
}

export interface UpdateOrganizationRequest {
  name?: string;
}

export interface UpdateOrganizationStatusRequest {
  status: OrganizationStatus;
}

export interface InviteMemberRequest {
  email: string;
  role_name: string;
}

export interface UpdateMembershipStatusRequest {
  status: MembershipStatus;
}

export interface AssignOrgRoleRequest {
  role_name: string;
}

export interface AcceptInvitationRequest {
  token: string;
  password?: string;
  first_name?: string;
  last_name?: string;
}

export interface SelectOrganizationRequest {
  organization_id: string;
}

export interface OrganizationContext {
  organization: Organization;
  membership_id: string | null;
  is_platform_super_admin: boolean;
  permissions: string[];
}
