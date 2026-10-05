"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Building2,
  LayoutDashboard,
  ShieldCheck,
  Users,
  LogOut,
  Smartphone,
  KeyRound,
  FolderKanban,
  FileCheck2,
  ListChecks,
  Radio,
  Link2,
  Activity,
  AppWindow,
} from "lucide-react";
import { useCurrentUser, useLogout } from "@/lib/auth";
import { useOrganizations, useSelectOrganization } from "@/lib/organizations";
import {
  subscribeActiveOrganizationId,
  getSnapshot,
  getServerSnapshot,
} from "@/lib/organization-store";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/organizations", label: "Organizations", icon: Building2 },
  { href: "/devices", label: "Devices", icon: Smartphone },
  { href: "/device-groups", label: "Groups", icon: FolderKanban },
  { href: "/enrollment", label: "Enrollment", icon: KeyRound },
  { href: "/policies", label: "Policies", icon: ShieldCheck },
  { href: "/applications", label: "Applications", icon: AppWindow },
  { href: "/compliance", label: "Compliance", icon: FileCheck2 },
  { href: "/jobs", label: "Jobs", icon: ListChecks },
  { href: "/provider-events", label: "Provider Events", icon: Radio },
  { href: "/provider-device-links", label: "Device Links", icon: Link2 },
  { href: "/providers", label: "Providers", icon: Activity },
  { href: "/users", label: "Users", icon: Users },
  { href: "/settings/security", label: "Security", icon: ShieldCheck },
];

function useActiveOrganizationId(): string | null {
  return useSyncExternalStore(subscribeActiveOrganizationId, getSnapshot, getServerSnapshot);
}

function OrganizationSelector() {
  const { data: organizations, isLoading } = useOrganizations();
  const selectOrganization = useSelectOrganization();
  const activeOrganizationId = useActiveOrganizationId();
  const [hasAutoSelected, setHasAutoSelected] = useState(false);

  // Zero-friction default: if the user belongs to exactly one organization
  // and none is active yet, select it automatically.
  useEffect(() => {
    if (hasAutoSelected || activeOrganizationId || !organizations) return;
    const onlyOrg = organizations.length === 1 ? organizations[0] : undefined;
    if (onlyOrg) {
      selectOrganization.mutate({ organization_id: onlyOrg.id });
    }
    setHasAutoSelected(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [organizations, activeOrganizationId, hasAutoSelected]);

  if (isLoading) {
    return <div className="h-9 w-48 animate-pulse rounded-lg bg-slate-100" />;
  }

  if (!organizations || organizations.length === 0) {
    return <span className="text-sm text-slate-400">No organizations</span>;
  }

  return (
    <select
      value={activeOrganizationId ?? ""}
      onChange={(e) => {
        const organizationId = e.target.value;
        if (organizationId) {
          selectOrganization.mutate({ organization_id: organizationId });
        }
      }}
      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 focus:border-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-800"
    >
      <option value="" disabled>
        Select organization…
      </option>
      {organizations.map((org) => (
        <option key={org.id} value={org.id}>
          {org.name}
        </option>
      ))}
    </select>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: me } = useCurrentUser();
  const logout = useLogout();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-sm font-bold tracking-tight text-slate-900">EnterpriseMDM</span>
            <nav className="hidden items-center gap-1 md:flex">
              {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
                const active = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium ${
                      active
                        ? "bg-slate-900 text-white"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <OrganizationSelector />
            {me?.user && (
              <span className="hidden text-sm text-slate-500 sm:inline">{me.user.email}</span>
            )}
            <button
              onClick={() => logout.mutate()}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            >
              <LogOut className="h-4 w-4" />
              Logout
            </button>
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
