"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, setCsrfToken } from "@/lib/api/client";
import {
  MeResponse,
  LoginRequest,
  LoginResponse,
  Session,
} from "@/types/auth";
import { useRouter } from "next/navigation";

export const AUTH_KEYS = {
  me: ["auth", "me"] as const,
  sessions: ["auth", "sessions"] as const,
};

export function useCurrentUser() {
  return useQuery<MeResponse>({
    queryKey: AUTH_KEYS.me,
    queryFn: () => apiClient.get<MeResponse>("/api/v1/auth/me"),
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation<LoginResponse, Error, LoginRequest>({
    mutationFn: (credentials) =>
      apiClient.post<LoginResponse>("/api/v1/auth/login", credentials),
    onSuccess: (data) => {
      if (data.csrf_token) {
        setCsrfToken(data.csrf_token);
      }
      queryClient.invalidateQueries({ queryKey: AUTH_KEYS.me });
      router.push("/dashboard");
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation<void, Error, void>({
    mutationFn: () => apiClient.post<void>("/api/v1/auth/logout"),
    onSettled: () => {
      setCsrfToken(null);
      queryClient.clear();
      router.push("/login");
    },
  });
}

export function useLogoutAll() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation<void, Error, void>({
    mutationFn: () => apiClient.post<void>("/api/v1/auth/logout-all"),
    onSettled: () => {
      setCsrfToken(null);
      queryClient.clear();
      router.push("/login");
    },
  });
}

export function useSessions() {
  return useQuery<Session[]>({
    queryKey: AUTH_KEYS.sessions,
    queryFn: () => apiClient.get<Session[]>("/api/v1/auth/sessions"),
  });
}

export function useRevokeSession() {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (sessionId: string) =>
      apiClient.delete<void>(`/api/v1/auth/sessions/${sessionId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_KEYS.sessions });
    },
  });
}
