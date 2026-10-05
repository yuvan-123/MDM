export interface User {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  full_name: string;
  status: string;
  is_email_verified: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Session {
  id: string;
  created_at: string;
  expires_at: string;
  last_used_at: string;
  ip_address: string | null;
  user_agent: string | null;
  device_identifier: string | null;
  is_current: boolean;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  user: User;
  csrf_token: string;
  access_token?: string | null;
  token_type?: string;
}

export interface MeResponse {
  user: User;
  session_id: string;
  permissions: string[];
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  token: string;
  new_password: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  request_id?: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}
