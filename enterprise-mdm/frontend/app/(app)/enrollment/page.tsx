"use client";

import { useState, FormEvent } from "react";
import { KeyRound, Plus, Loader2, AlertCircle, Copy, Ban, Check } from "lucide-react";
import { useEnrollmentTokens, useCreateEnrollmentToken, useRevokeEnrollmentToken } from "@/lib/enrollment";
import { ApiError } from "@/lib/api/client";
import { EnrollmentTokenStatus } from "@/types/enrollment";
import { DevicePlatform } from "@/types/device";

const STATUS_BADGE: Record<EnrollmentTokenStatus, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REVOKED: "bg-red-50 text-red-700 border-red-200",
  EXPIRED: "bg-slate-100 text-slate-500 border-slate-200",
  EXHAUSTED: "bg-amber-50 text-amber-700 border-amber-200",
};

const PLATFORMS: DevicePlatform[] = ["ANDROID", "IOS", "IPADOS", "OTHER"];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function EnrollmentPage() {
  const { data: tokens, isLoading, isError } = useEnrollmentTokens();
  const createToken = useCreateEnrollmentToken();
  const revokeToken = useRevokeEnrollmentToken();

  const [label, setLabel] = useState("");
  const [expiresInHours, setExpiresInHours] = useState(24);
  const [requireApproval, setRequireApproval] = useState(true);
  const [maxUses, setMaxUses] = useState(1);
  const [allowedPlatform, setAllowedPlatform] = useState<DevicePlatform | "">("");
  const [provisionViaProvider, setProvisionViaProvider] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [newProviderValue, setNewProviderValue] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNewToken(null);
    setNewProviderValue(null);
    try {
      const result = await createToken.mutateAsync({
        label: label.trim() || undefined,
        expires_in_hours: expiresInHours,
        require_approval: requireApproval,
        max_uses: maxUses,
        allowed_platform: allowedPlatform || undefined,
        provision_via_provider: provisionViaProvider,
      });
      setNewToken(result.token);
      setNewProviderValue(result.provider_enrollment_value);
      setLabel("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create enrollment token.");
    }
  };

  const handleCopy = (value: string, onDone: () => void) => {
    navigator.clipboard.writeText(value);
    onDone();
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Enrollment Tokens</h1>
        <p className="mt-1 text-sm text-slate-600">
          Generate tokens for devices to self-register with{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">POST /enrollment/register</code>.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Generate a token</h2>
        <form onSubmit={handleCreate} className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div className="col-span-2 sm:col-span-1">
            <label className="block text-xs font-medium text-slate-600">Label</label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Sales team"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Expires in (hours)</label>
            <input
              type="number"
              min={1}
              max={2160}
              value={expiresInHours}
              onChange={(e) => setExpiresInHours(Number(e.target.value))}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Max uses</label>
            <input
              type="number"
              min={1}
              max={10000}
              value={maxUses}
              onChange={(e) => setMaxUses(Number(e.target.value))}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={requireApproval}
                onChange={(e) => setRequireApproval(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-800"
              />
              Require approval
            </label>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Platform</label>
            <select
              value={allowedPlatform}
              onChange={(e) => {
                const value = e.target.value as DevicePlatform | "";
                setAllowedPlatform(value);
                if (!value) setProvisionViaProvider(false);
              }}
              className="mt-1.5 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              <option value="">Any platform</option>
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="col-span-2 flex items-end sm:col-span-3">
            <label
              className={`flex items-center gap-2 text-sm ${allowedPlatform ? "text-slate-700" : "text-slate-400"}`}
            >
              <input
                type="checkbox"
                checked={provisionViaProvider}
                disabled={!allowedPlatform}
                onChange={(e) => setProvisionViaProvider(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-800 disabled:opacity-50"
              />
              Also mint a real provider enrollment credential (requires a platform)
            </label>
          </div>
          <div className="col-span-2 sm:col-span-4">
            <button
              type="submit"
              disabled={createToken.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
            >
              {createToken.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              Generate token
            </button>
          </div>
        </form>

        {error && (
          <p role="alert" className="mt-3 flex items-center gap-1.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            {error}
          </p>
        )}

        {newToken && (
          <div className="mt-4 space-y-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
            <div>
              <p className="text-xs font-medium text-emerald-800">
                Token created — shown once, copy it now:
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 truncate rounded bg-white px-3 py-2 text-xs text-slate-900">
                  {newToken}
                </code>
                <button
                  onClick={() => handleCopy(newToken, () => { setCopied(true); setTimeout(() => setCopied(false), 2000); })}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-white px-2.5 py-2 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                >
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
            {newProviderValue && (
              <div>
                <p className="text-xs font-medium text-emerald-800">
                  Real provider enrollment credential — shown once, hand this to the device
                  directly (QR/provisioning flow), never through this app&apos;s own registration
                  endpoint:
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <code className="flex-1 truncate rounded bg-white px-3 py-2 text-xs text-slate-900">
                    {newProviderValue}
                  </code>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading tokens…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load enrollment tokens.</p>
        ) : tokens && tokens.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {tokens.map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-4 px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                    <KeyRound className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <p className="text-sm font-medium text-slate-900">
                        {token.label || "Unlabeled token"}
                      </p>
                      {token.provision_via_provider && (
                        <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                          provider-provisioned
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-400">
                      {token.use_count}/{token.max_uses} used · expires {formatDate(token.expires_at)}
                      {!token.require_approval && " · auto-enrolls"}
                      {token.allowed_platform && ` · ${token.allowed_platform}`}
                    </p>
                    {token.provider_enrollment_reference && (
                      <p className="mt-0.5 truncate text-xs text-slate-400">
                        provider ref: {token.provider_enrollment_reference}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE[token.status]}`}
                  >
                    {token.status}
                  </span>
                  {token.status === "ACTIVE" && (
                    <button
                      onClick={() => revokeToken.mutate(token.id)}
                      disabled={revokeToken.isPending}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
                    >
                      <Ban className="h-3.5 w-3.5" />
                      Revoke
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No enrollment tokens yet.</p>
        )}
      </section>
    </main>
  );
}
