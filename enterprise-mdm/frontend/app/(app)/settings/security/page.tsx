"use client";

import { useState, FormEvent } from "react";
import { useSessions, useRevokeSession, useLogoutAll } from "@/lib/auth";
import { apiClient, ApiError } from "@/lib/api/client";
import {
  KeyRound,
  Laptop,
  Globe,
  Clock,
  LogOut,
  Trash2,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ShieldAlert,
  Smartphone,
  Fingerprint,
} from "lucide-react";

export default function SecuritySettingsPage() {
  const { data: sessions, isLoading: sessionsLoading, isError: sessionsError } = useSessions();
  const revokeSessionMutation = useRevokeSession();
  const logoutAllMutation = useLogoutAll();

  // Change Password state
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);

    if (newPassword.length < 12) {
      setPasswordError("New password must be at least 12 characters long.");
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match.");
      return;
    }

    setPasswordLoading(true);

    try {
      await apiClient.post("/api/v1/auth/change-password", {
        old_password: oldPassword,
        new_password: newPassword,
      });
      setPasswordSuccess("Password updated successfully. Other active sessions have been invalidated.");
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      if (err instanceof ApiError) {
        setPasswordError(err.message);
      } else {
        setPasswordError("Failed to update password. Please check your old password.");
      }
    } finally {
      setPasswordLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-10 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Security Settings</h1>
        <p className="mt-1 text-sm text-slate-600">
          Manage your password, active browser sessions, and security preferences.
        </p>
      </div>

      {/* 1. Change Password Section */}
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-6">
        <div className="flex items-center gap-3 border-b border-slate-100 pb-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-900">
            <KeyRound className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Change Password</h2>
            <p className="text-xs text-slate-500">
              Ensure your account is using a long, random password to stay secure.
            </p>
          </div>
        </div>

        {passwordSuccess && (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
          >
            <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600 mt-0.5" />
            <div>
              <p className="font-medium">Success</p>
              <p className="mt-0.5 text-xs text-emerald-800">{passwordSuccess}</p>
            </div>
          </div>
        )}

        {passwordError && (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
          >
            <AlertCircle className="h-5 w-5 shrink-0 text-red-600 mt-0.5" />
            <div>
              <p className="font-medium">Error updating password</p>
              <p className="mt-0.5 text-xs text-red-700">{passwordError}</p>
            </div>
          </div>
        )}

        <form onSubmit={handleChangePassword} className="space-y-4 max-w-lg">
          <div>
            <label
              htmlFor="old-password"
              className="block text-sm font-medium text-slate-700"
            >
              Current Password
            </label>
            <input
              id="old-password"
              type="password"
              required
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              disabled={passwordLoading}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800 disabled:bg-slate-50"
              placeholder="••••••••••••"
            />
          </div>

          <div>
            <label
              htmlFor="new-password"
              className="block text-sm font-medium text-slate-700"
            >
              New Password
            </label>
            <input
              id="new-password"
              type="password"
              required
              minLength={12}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={passwordLoading}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800 disabled:bg-slate-50"
              placeholder="At least 12 characters"
            />
          </div>

          <div>
            <label
              htmlFor="confirm-password"
              className="block text-sm font-medium text-slate-700"
            >
              Confirm New Password
            </label>
            <input
              id="confirm-password"
              type="password"
              required
              minLength={12}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={passwordLoading}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800 disabled:bg-slate-50"
              placeholder="••••••••••••"
            />
          </div>

          <div className="pt-2">
            <button
              type="submit"
              disabled={passwordLoading}
              className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 disabled:opacity-70"
            >
              {passwordLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Updating Password...
                </>
              ) : (
                "Update Password"
              )}
            </button>
          </div>
        </form>
      </section>

      {/* 2. Active Sessions Section */}
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-6">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-900">
              <Laptop className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Active Sessions</h2>
              <p className="text-xs text-slate-500">
                Devices currently signed into your EnterpriseMDM account.
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => logoutAllMutation.mutate()}
            disabled={logoutAllMutation.isPending}
            className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-100 hover:border-red-300 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-60"
          >
            {logoutAllMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <LogOut className="h-3.5 w-3.5" />
            )}
            Logout All Sessions
          </button>
        </div>

        {sessionsLoading && (
          <div className="flex items-center justify-center py-8 text-slate-500">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading active sessions...
          </div>
        )}

        {sessionsError && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            Failed to load active sessions. Please refresh the page.
          </div>
        )}

        {sessions && sessions.length > 0 && (
          <div className="divide-y divide-slate-100">
            {sessions.map((session) => (
              <div
                key={session.id}
                className="flex items-center justify-between py-4 first:pt-0 last:pb-0"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-slate-900">
                      {session.user_agent || "Unknown Browser"}
                    </span>
                    {session.is_current && (
                      <span className="inline-flex items-center rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">
                        Current session
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                    <span className="flex items-center gap-1">
                      <Globe className="h-3.5 w-3.5 text-slate-400" />
                      {session.ip_address || "Unknown IP"}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock className="h-3.5 w-3.5 text-slate-400" />
                      Last active: {new Date(session.last_used_at).toLocaleString()}
                    </span>
                  </div>
                </div>

                {!session.is_current && (
                  <button
                    type="button"
                    onClick={() => revokeSessionMutation.mutate(session.id)}
                    disabled={revokeSessionMutation.isPending}
                    className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:text-red-600 focus:outline-none disabled:opacity-50"
                  >
                    {revokeSessionMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 3. MFA Planned Notice Section */}
      <section className="rounded-xl border border-amber-200 bg-amber-50/60 p-6 shadow-sm space-y-4">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-900">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-slate-900">
                Multi-Factor Authentication (MFA)
              </h2>
              <span className="inline-flex items-center rounded-md bg-amber-200/80 px-2 py-0.5 text-xs font-bold tracking-wide text-amber-900 uppercase">
                PLANNED / NOT YET IMPLEMENTED
              </span>
            </div>

            <p className="text-sm text-slate-700">
              Enhanced multi-factor authentication features are currently under development and scheduled for a future platform update.
            </p>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 pt-2">
              <div className="flex items-start gap-3 rounded-lg border border-amber-200/80 bg-white p-3.5">
                <Smartphone className="h-5 w-5 text-amber-700 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-xs font-semibold text-slate-900">TOTP Authenticator Apps</h3>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Planned support for Google Authenticator, 1Password, and 2FA TOTP standard tokens.
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-amber-200/80 bg-white p-3.5">
                <Fingerprint className="h-5 w-5 text-amber-700 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-xs font-semibold text-slate-900">WebAuthn & Hardware Keys</h3>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Planned support for FIDO2 YubiKeys, Touch ID, Face ID, and Windows Hello biometric logins.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
