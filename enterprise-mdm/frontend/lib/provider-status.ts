"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { ProviderStatusResponse } from "@/types/provider-status";

// Platform-level, not organization-scoped (Android/Apple credentials are
// deployment-wide settings, gated by AUDIT_READ i.e. SUPER_ADMIN) — unlike
// every other query in lib/, this one deliberately has no organization_id
// in its key and no `enabled: !!organizationId` guard.
export function useProviderStatus() {
  return useQuery<ProviderStatusResponse>({
    queryKey: ["provider-status"],
    queryFn: () => apiClient.get<ProviderStatusResponse>("/api/v1/providers/status"),
  });
}
