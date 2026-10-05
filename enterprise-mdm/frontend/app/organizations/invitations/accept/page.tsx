"use client";

import { Suspense, useState, FormEvent } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Building2, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { useAcceptInvitation } from "@/lib/organizations";
import { ApiError } from "@/lib/api/client";

/**
 * Unauthenticated (not under app/(app)) — this is the link recipients open
 * from their invitation email. The invitation token itself is the proof
 * of authorization; no prior login is required. If the invited email
 * doesn't have an account yet, the backend creates one from the password
 * submitted here (see app/organizations/membership_service.py::accept_invitation).
 */
function AcceptInvitationForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token") ?? "";

  const acceptInvitation = useAcceptInvitation();
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await acceptInvitation.mutateAsync({
        token,
        password: password || undefined,
        first_name: firstName || undefined,
        last_name: lastName || undefined,
      });
      setSuccess(true);
      setTimeout(() => router.push("/login"), 2000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to accept invitation.");
    }
  };

  if (!token) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-red-600">
        <AlertCircle className="h-4 w-4" />
        Missing invitation token.
      </p>
    );
  }

  if (success) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-emerald-700">
        <CheckCircle2 className="h-4 w-4" />
        Invitation accepted. Redirecting to login…
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <p className="text-sm text-slate-600">
        If you already have an EnterpriseMDM account with this email, leave the fields below
        blank and submit to join. If this is your first time, set a password to create your
        account.
      </p>
      <div>
        <label className="block text-xs font-medium text-slate-600">
          Password (only if creating a new account)
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={12}
          className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-600">First name</label>
          <input
            type="text"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Last name</label>
          <input
            type="text"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
          />
        </div>
      </div>
      {error && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={acceptInvitation.isPending}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-70"
      >
        {acceptInvitation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        Accept invitation
      </button>
    </form>
  );
}

export default function AcceptInvitationPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md items-center px-4">
      <div className="w-full space-y-6 rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
            <Building2 className="h-5 w-5" />
          </div>
          <h1 className="text-xl font-bold text-slate-900">Join organization</h1>
        </div>
        <Suspense fallback={<Loader2 className="h-4 w-4 animate-spin" />}>
          <AcceptInvitationForm />
        </Suspense>
      </div>
    </main>
  );
}
