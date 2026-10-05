import { ApiErrorResponse } from "@/types/auth";
import { getActiveOrganizationId } from "@/lib/organization-store";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

let memoryCsrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  memoryCsrfToken = token;
}

export function getCsrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

export function getCsrfToken(): string | null {
  return memoryCsrfToken || getCsrfTokenFromCookie();
}

export class ApiError extends Error {
  code: string;
  status: number;
  requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined>;
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { body, params, headers: customHeaders, method = "GET", ...restOptions } = options;

  let url = endpoint.startsWith("http://") || endpoint.startsWith("https://")
    ? endpoint
    : `${API_BASE_URL.replace(/\/$/, "")}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

  if (params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value));
      }
    });
    const queryString = searchParams.toString();
    if (queryString) {
      url += (url.includes("?") ? "&" : "?") + queryString;
    }
  }

  const headers = new Headers(customHeaders);

  if (body !== undefined && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const upperMethod = method.toUpperCase();
  if (["POST", "PATCH", "PUT", "DELETE"].includes(upperMethod)) {
    const csrfToken = getCsrfToken();
    if (csrfToken && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  // Tenant context: a routing hint only — the backend re-validates real
  // membership on every request (see docs/architecture/multi-tenancy.md).
  if (!headers.has("X-Organization-ID")) {
    const activeOrganizationId = getActiveOrganizationId();
    if (activeOrganizationId) {
      headers.set("X-Organization-ID", activeOrganizationId);
    }
  }

  const response = await fetch(url, {
    ...restOptions,
    method: upperMethod,
    headers,
    credentials: "include",
    body: body !== undefined && !(body instanceof FormData) ? JSON.stringify(body) : (body as BodyInit | null),
  });

  if (!response.ok) {
    if (response.status === 401) {
      if (typeof window !== "undefined" && window.location.pathname !== "/login" && !endpoint.includes("/auth/login")) {
        window.location.href = "/login";
      }
    }

    let errorPayload: ApiErrorResponse | null = null;
    try {
      errorPayload = await response.json();
    } catch {
      // Ignore JSON parse errors
    }

    if (errorPayload?.error) {
      throw new ApiError(
        response.status,
        errorPayload.error.code,
        errorPayload.error.message,
        errorPayload.error.request_id
      );
    }

    throw new ApiError(
      response.status,
      "HTTP_ERROR",
      `Request failed with status ${response.status}`
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

export const apiClient = {
  get: <T>(endpoint: string, options?: RequestOptions) => request<T>(endpoint, { ...options, method: "GET" }),
  post: <T>(endpoint: string, body?: unknown, options?: RequestOptions) => request<T>(endpoint, { ...options, body, method: "POST" }),
  put: <T>(endpoint: string, body?: unknown, options?: RequestOptions) => request<T>(endpoint, { ...options, body, method: "PUT" }),
  patch: <T>(endpoint: string, body?: unknown, options?: RequestOptions) => request<T>(endpoint, { ...options, body, method: "PATCH" }),
  delete: <T>(endpoint: string, options?: RequestOptions) => request<T>(endpoint, { ...options, method: "DELETE" }),
};
