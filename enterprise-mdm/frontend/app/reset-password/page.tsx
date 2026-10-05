"use client";

import { useState, FormEvent, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { apiClient, ApiError } from "@/lib/api/client";
import { Shield, CheckCircle2, AlertCircle, Loader2, ArrowLeft } from "lucide-react";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    if (!token) {
      setError("Missing reset token.");
      return;
    }

    if (newPassword.length < 12) {
      setError("Password must be at least 12 characters long.");
      return;
    }

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await apiClient.post("/api/v1/auth/reset-password", {
        token,
        new_password: newPassword,
      });
      setSuccess(true);
      setTimeout(() => {
        router.push("/login");
      }, 3000);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to reset password. The link may have expired.");
      }
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="space-y-6">
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <AlertCircle className="h-5 w-5 shrink-0 text-red-600 mt-0.5" />
          <div>
            <p className="font-medium">Invalid Request</p>
            <p className="mt-0.5 text-xs text-red-700">
              No password reset token was provided. Please check your email link or request a new reset.
            </p>
          </div>
        </div>
        <Link
          href="/forgot-password"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 focus:outline-none"
        >
          Request new reset link
        </Link>
      </div>
    );
  }

  if (success) {
    return (
      <div className="space-y-6">
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
        >
          <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600 mt-0.5" />
          <div>
            <p className="font-medium">Password Reset Complete</p>
            <p className="mt-1 text-xs text-emerald-800">
              Your password has been reset successfully. Redirecting you to login...
            </p>
          </div>
        </div>

        <Link
          href="/login"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 focus:outline-none"
        >
          Go to Sign in
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <AlertCircle className="h-5 w-5 shrink-0 text-red-600 mt-0.5" />
          <div>
            <p className="font-medium">Error</p>
            <p className="mt-0.5 text-xs text-red-700">{error}</p>
          </div>
        </div>
      )}

      <div>
        <label
          htmlFor="new-password"
          className="block text-sm font-medium text-slate-700"
        >
          New Password
        </label>
        <input
          id="new-password"
          name="newPassword"
          type="password"
          required
          minLength={12}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          disabled={loading}
          className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800 disabled:bg-slate-50 disabled:text-slate-500"
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
          name="confirmPassword"
          type="password"
          required
          minLength={12}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          disabled={loading}
          className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800 disabled:bg-slate-50 disabled:text-slate-500"
          placeholder="••••••••••••"
        />
      </div>

      <button
        type="submit"
        disabled={loading}
        className="flex w-full items-center justify-center rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-70"
      >
        {loading ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Resetting password...
          </>
        ) : (
          "Reset Password"
        )}
      </button>

      <div className="text-center">
        <Link
          href="/login"
          className="inline-flex items-center text-xs font-medium text-slate-600 hover:text-slate-900"
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" />
          Back to Sign in
        </Link>
      </div>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-900 text-white shadow-sm">
            <Shield className="h-6 w-6" />
          </div>
          <h1 className="mt-4 text-2xl font-bold tracking-tight text-slate-900">
            Set New Password
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Choose a strong password with at least 12 characters
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
          <Suspense
            fallback={
              <div className="flex items-center justify-center p-6">
                <Loader2 className="h-6 w-6 animate-spin text-slate-600" />
              </div>
            }
          >
            <ResetPasswordForm />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
