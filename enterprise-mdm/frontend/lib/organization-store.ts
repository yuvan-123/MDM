"use client";

import { useSyncExternalStore } from "react";

/**
 * Tracks which organization is "active" for the current browser tab.
 *
 * Deliberately NOT server-side session state (see docs/architecture/multi-tenancy.md):
 * the backend never trusts this value for authorization — every tenant-scoped
 * request that carries it as the X-Organization-ID header is re-validated
 * against real membership on every call. This store only decides which
 * value gets attached to that header; picking the wrong one just gets a
 * 403 back, it never grants access.
 *
 * Module-level (not React Context) so lib/api/client.ts can read it
 * synchronously before any component has rendered.
 */

const STORAGE_KEY = "enterprise-mdm:active-organization-id";

type Listener = () => void;

let activeOrganizationId: string | null = null;
const listeners = new Set<Listener>();

function readFromStorage(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

if (typeof window !== "undefined") {
  activeOrganizationId = readFromStorage();
}

export function getActiveOrganizationId(): string | null {
  return activeOrganizationId;
}

export function setActiveOrganizationId(organizationId: string | null): void {
  activeOrganizationId = organizationId;
  if (typeof window !== "undefined") {
    if (organizationId) {
      window.localStorage.setItem(STORAGE_KEY, organizationId);
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }
  listeners.forEach((listener) => listener());
}

export function clearActiveOrganizationId(): void {
  setActiveOrganizationId(null);
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): string | null {
  return activeOrganizationId;
}

function getServerSnapshot(): string | null {
  return null;
}

export { subscribe as subscribeActiveOrganizationId, getSnapshot, getServerSnapshot };

/**
 * Reactive read of the active organization id — re-renders the calling
 * component when it changes (e.g. via the header org selector), unlike a
 * bare `getActiveOrganizationId()` call. Use this in any hook/component
 * whose data (or React Query key) depends on the current organization,
 * such as the Phase 4 device hooks in lib/devices.ts.
 */
export function useActiveOrganizationId(): string | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
