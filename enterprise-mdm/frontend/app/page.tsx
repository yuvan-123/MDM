import Link from "next/link";

export default function HomePage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="max-w-md rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-2xl font-semibold text-slate-900">EnterpriseMDM</h1>
        <p className="mt-2 text-sm text-slate-500">
          Multi-tenant Enterprise Mobile Device Management — Phase 1 foundation.
        </p>
        <Link
          href="/dashboard"
          className="mt-6 inline-block rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Go to dashboard shell
        </Link>
      </div>
    </main>
  );
}
