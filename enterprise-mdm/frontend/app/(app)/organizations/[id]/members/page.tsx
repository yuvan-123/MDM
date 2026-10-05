"use client";

import { useState, FormEvent } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { UserPlus, Trash2, Loader2, AlertCircle, CheckCircle2, Shield } from "lucide-react";
import {
  useOrganizationMembers,
  useInviteMember,
  useRemoveMember,
  useUpdateMemberStatus,
  useAssignMemberRole,
  useRemoveMemberRole,
} from "@/lib/organizations";
import { ApiError } from "@/lib/api/client";
import { MembershipStatus } from "@/types/organization";

const ASSIGNABLE_ROLES = ["ORGANIZATION_ADMIN", "SECURITY_ADMIN", "HELP_DESK", "AUDITOR"];

const STATUS_BADGE: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  INVITED: "bg-blue-50 text-blue-700 border-blue-200",
  SUSPENDED: "bg-amber-50 text-amber-700 border-amber-200",
  REMOVED: "bg-slate-100 text-slate-500 border-slate-200",
};

export default function OrganizationMembersPage() {
  const params = useParams<{ id: string }>();
  const organizationId = params.id;

  const { data: members, isLoading, isError } = useOrganizationMembers(organizationId);
  const inviteMember = useInviteMember(organizationId);
  const removeMember = useRemoveMember(organizationId);
  const updateMemberStatus = useUpdateMemberStatus(organizationId);
  const assignRole = useAssignMemberRole(organizationId);
  const removeRole = useRemoveMemberRole(organizationId);

  const [email, setEmail] = useState("");
  const [roleName, setRoleName] = useState<string>(ASSIGNABLE_ROLES[2] ?? "HELP_DESK");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (!email.trim()) return;
    try {
      await inviteMember.mutateAsync({ email: email.trim(), role_name: roleName });
      setSuccess(`Invitation sent to ${email.trim()}.`);
      setEmail("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to send invitation.");
    }
  };

  const handleRemove = async (membershipId: string) => {
    setError(null);
    try {
      await removeMember.mutateAsync(membershipId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove member.");
    }
  };

  const handleToggleSuspend = async (membershipId: string, current: MembershipStatus) => {
    setError(null);
    const next: MembershipStatus = current === "SUSPENDED" ? "ACTIVE" : "SUSPENDED";
    try {
      await updateMemberStatus.mutateAsync({ membershipId, body: { status: next } });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update member status.");
    }
  };

  const handleAssignRole = async (membershipId: string) => {
    setError(null);
    try {
      await assignRole.mutateAsync({ membershipId, body: { role_name: roleName } });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to assign role.");
    }
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <div>
        <Link
          href={`/organizations/${organizationId}`}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          ← Back to organization
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-slate-900">Members</h1>
      </div>

      {(error || success) && (
        <div
          role="alert"
          className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-red-200 bg-red-50 text-red-700"
              : "border-emerald-200 bg-emerald-50 text-emerald-700"
          }`}
        >
          {error ? <AlertCircle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
          {error ?? success}
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Invite a member</h2>
        <form onSubmit={handleInvite} className="mt-4 flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-600">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">Role</label>
            <select
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              className="mt-1.5 block rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
            >
              {ASSIGNABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={inviteMember.isPending || !email.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {inviteMember.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            Invite
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="p-6 text-sm text-slate-500">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading members…
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-red-600">Failed to load members.</p>
        ) : members && members.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {members.map((member) => (
              <li key={member.id} className="flex items-center justify-between gap-4 px-6 py-4">
                <div>
                  <p className="text-sm font-medium text-slate-900">{member.user_full_name}</p>
                  <p className="text-xs text-slate-400">{member.user_email}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {member.roles.map((role) => (
                      <span
                        key={role.id}
                        className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600"
                      >
                        <Shield className="h-3 w-3" />
                        {role.role_name}
                        <button
                          onClick={() => removeRole.mutate({ membershipId: member.id, roleId: role.id })}
                          className="ml-0.5 text-slate-400 hover:text-red-600"
                          title="Remove role"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                      STATUS_BADGE[member.status] ?? "border-slate-200 text-slate-600"
                    }`}
                  >
                    {member.status}
                  </span>
                  <button
                    onClick={() => handleAssignRole(member.id)}
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    + Role
                  </button>
                  <button
                    onClick={() => handleToggleSuspend(member.id, member.status)}
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    {member.status === "SUSPENDED" ? "Reactivate" : "Suspend"}
                  </button>
                  <button
                    onClick={() => handleRemove(member.id)}
                    className="rounded-lg border border-red-200 bg-red-50 p-1.5 text-red-700 hover:bg-red-100"
                    title="Remove member"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-6 text-sm text-slate-500">No members yet.</p>
        )}
      </section>
    </main>
  );
}
