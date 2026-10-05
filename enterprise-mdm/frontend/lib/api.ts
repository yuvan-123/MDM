/**
 * Minimal typed fetch wrapper for the backend API.
 *
 * Phase 1 note: no auth token attachment yet (Phase 2 adds session/JWT
 * handling here). This module exists now so pages have a single place to
 * call the API from, rather than scattering fetch() calls.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  dependencies: Record<string, "ok" | "unavailable">;
}

export async function fetchReadiness(): Promise<ReadinessResponse> {
  const res = await fetch(`${API_BASE_URL}/ready`, { cache: "no-store" });
  return res.json();
}
