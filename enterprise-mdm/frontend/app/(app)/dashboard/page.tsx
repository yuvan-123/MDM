import { fetchReadiness } from "@/lib/api";

/**
 * Dashboard shell — Phase 1 only shows backend connectivity status.
 * Real dashboard cards (device counts, compliance, etc.) arrive in
 * Phase 4 once the device inventory domain exists.
 */
export default async function DashboardPage() {
  let readiness;
  let fetchError: string | null = null;

  try {
    readiness = await fetchReadiness();
  } catch {
    fetchError = "Could not reach backend API.";
  }

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <h1 className="text-xl font-semibold text-slate-900">Dashboard</h1>
      <p className="mt-1 text-sm text-slate-500">
        Placeholder shell. Device/compliance metrics arrive in Phase 4.
      </p>

      <section className="mt-6 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-medium text-slate-700">Backend connectivity</h2>
        {fetchError ? (
          <p className="mt-2 text-sm text-red-600">{fetchError}</p>
        ) : (
          <dl className="mt-3 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-500">Overall status</dt>
              <dd className="font-medium text-slate-900">{readiness?.status}</dd>
            </div>
            {readiness &&
              Object.entries(readiness.dependencies).map(([name, status]) => (
                <div key={name}>
                  <dt className="text-slate-500 capitalize">{name}</dt>
                  <dd
                    className={
                      status === "ok" ? "font-medium text-emerald-600" : "font-medium text-red-600"
                    }
                  >
                    {status}
                  </dd>
                </div>
              ))}
          </dl>
        )}
      </section>
    </main>
  );
}
