"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWidgetData } from "../hooks/useWidgetData";
import type { InstallResult, InstallStatus, InstallStep } from "../types";

const SCHEDULES = [
  { label: "On demand only", cron: "" },
  { label: "Hourly", cron: "0 0 * * * ?" },
  { label: "Daily at 06:00 UTC", cron: "0 0 6 * * ?" },
  { label: "Weekly, Monday 06:00 UTC", cron: "0 0 6 ? * MON" },
];

const CHIP: Record<InstallStep["status"], string> = {
  created: "bg-emerald-400/10 text-emerald-400",
  updated: "bg-sky-400/10 text-sky-400",
  unchanged: "bg-white/5 text-white/50",
  failed: "bg-red-400/10 text-red-400",
};

const inputCls = "w-full bg-[#09090b] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-[#3b82f6]";

export default function WorkspaceSetupPage() {
  const status = useWidgetData<InstallStatus>((signal) => api.get<InstallStatus>("/api/v1/databricks/install/status", { signal }), []);
  const signedIn = status.data?.signed_in_with_databricks ?? false;
  const catalogs = useWidgetData<{ catalogs: string[] }>(
    (signal) => (signedIn ? api.get<{ catalogs: string[] }>("/api/v1/databricks/install/catalogs", { signal }) : Promise.resolve({ catalogs: [] })),
    [signedIn],
  );

  const [catalog, setCatalog] = useState("");
  const [prefix, setPrefix] = useState("dataone");
  const [cron, setCron] = useState("");
  const [busy, setBusy] = useState<"install" | "run" | null>(null);
  const [result, setResult] = useState<InstallResult | null>(null);
  const [runUrl, setRunUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const installed = status.data?.installed ?? null;
  useEffect(() => {
    if (installed) { setCatalog(installed.catalog); setPrefix(installed.schema_prefix); }
  }, [installed]);
  useEffect(() => {
    const names = catalogs.data?.catalogs ?? [];
    if (!catalog && names.length) setCatalog(names.includes("workspace") ? "workspace" : names[0]);
  }, [catalogs.data, catalog]);

  async function install() {
    setBusy("install"); setError(null); setResult(null); setRunUrl(null);
    try {
      const res = await api.post<InstallResult>("/api/v1/databricks/install", {
        catalog, schema_prefix: prefix, schedule_cron: cron || null,
      });
      setResult(res);
      status.refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Install failed");
    } finally { setBusy(null); }
  }

  async function runNow() {
    setBusy("run"); setError(null);
    try {
      const res = await api.post<{ run_id: number; run_url: string }>("/api/v1/databricks/install/run", {});
      setRunUrl(res.run_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the job");
    } finally { setBusy(null); }
  }

  const s = status.data;
  const canInstall = !!s && signedIn && s.ingest_key_configured && !!catalog && !!prefix && busy === null;

  return (
    <div className="flex flex-col h-full bg-[#09090b] text-white">
      <div className="flex-1 overflow-y-auto p-6 lg:p-10 max-w-4xl mx-auto w-full">
        <h1 className="text-3xl font-bold tracking-tight mb-2">Workspace setup</h1>
        <p className="text-white/50 text-sm mb-8">
          Installs DataOne&apos;s data-quality notebook and job into <em>your</em> Databricks workspace, using your own
          Databricks sign-in. Schemas, the landing volume and the job are created in the catalog you choose; compute is yours.
          Running this again updates everything to the current release.
        </p>

        {status.isLoading && !s && <div className="bg-[#111] border border-white/5 rounded-xl h-40 animate-pulse" role="status" aria-label="Loading workspace status" />}
        {status.isError && !s && (
          <div className="bg-[#111] border border-white/5 rounded-xl p-6 text-sm text-red-400">
            Couldn&apos;t load status: {status.errorMessage} <button onClick={status.refetch} className="ml-2 text-[#3b82f6] hover:underline">Retry</button>
          </div>
        )}

        {s && (
          <div className="flex flex-col gap-6">
            <section className="bg-[#111] border border-white/5 rounded-xl p-6 flex flex-col gap-3 text-sm" aria-label="Connection">
              <div className="flex justify-between gap-4"><span className="text-white/50">Workspace</span><span className="truncate">{s.workspace_host ?? "Not configured"}</span></div>
              <div className="flex justify-between gap-4 items-center">
                <span className="text-white/50">Databricks sign-in</span>
                {signedIn ? <span className="text-emerald-400">Signed in</span> : (
                  <a className="px-3 py-1.5 rounded-lg bg-[#0ea5e9] hover:bg-[#0284c7] text-white font-semibold"
                     href={`${api.base}/api/v1/auth/databricks/login?redirect_to=/dashboard/workspace-setup`}>Sign in with Databricks</a>
                )}
              </div>
              {!s.ingest_key_configured && <p className="text-amber-400">The DataOne server has no DQ_INGEST_KEY configured, so the job could not report results.</p>}
              <div className="flex justify-between gap-4 items-center">
                <span className="text-white/50">Installed release</span>
                {installed ? (
                  <span>
                    {installed.release} · {installed.catalog} · {new Date(installed.installed_at).toLocaleString()}
                    {s.up_to_date ? <span className="ml-2 text-emerald-400">Up to date</span> : <span className="ml-2 text-amber-400">Update available ({s.current_release})</span>}
                  </span>
                ) : <span className="text-white/40">Not installed</span>}
              </div>
            </section>

            <section className="bg-[#111] border border-white/5 rounded-xl p-6 grid grid-cols-1 md:grid-cols-3 gap-4" aria-label="Install options">
              <label className="text-sm text-white/60 flex flex-col gap-1">Catalog
                <select className={inputCls} value={catalog} onChange={(e) => setCatalog(e.target.value)} disabled={!signedIn}>
                  {!catalog && <option value="">{signedIn ? "Select a catalog" : "Sign in first"}</option>}
                  {(catalogs.data?.catalogs ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
                  {catalog && !(catalogs.data?.catalogs ?? []).includes(catalog) && <option value={catalog}>{catalog}</option>}
                </select>
              </label>
              <label className="text-sm text-white/60 flex flex-col gap-1">Schema prefix
                <input className={inputCls} value={prefix} onChange={(e) => setPrefix(e.target.value.replace(/[^A-Za-z0-9_]/g, ""))} placeholder="dataone" />
                <span className="text-xs text-white/40">Creates {prefix || "…"}_bronze / _silver / _gold</span>
              </label>
              <label className="text-sm text-white/60 flex flex-col gap-1">Data quality schedule
                <select className={inputCls} value={cron} onChange={(e) => setCron(e.target.value)}>
                  {SCHEDULES.map((x) => <option key={x.label} value={x.cron}>{x.label}</option>)}
                </select>
              </label>
              <div className="md:col-span-3 flex flex-wrap gap-3 items-center">
                <button onClick={install} disabled={!canInstall}
                        className="px-5 py-2 rounded-lg bg-[#0ea5e9] hover:bg-[#0284c7] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold">
                  {busy === "install" ? "Installing…" : installed ? "Update installation" : "Install in my workspace"}
                </button>
                {installed?.job_id && (
                  <button onClick={runNow} disabled={busy !== null}
                          className="px-5 py-2 rounded-lg border border-white/10 hover:bg-white/5 disabled:opacity-40 text-sm font-semibold">
                    {busy === "run" ? "Starting…" : "Run data quality now"}
                  </button>
                )}
                {runUrl && <a className="text-sm text-[#3b82f6] hover:underline" href={runUrl} target="_blank" rel="noreferrer">Job started — view the run</a>}
              </div>
            </section>

            {error && <div role="alert" className="bg-red-400/10 border border-red-400/20 rounded-xl p-4 text-sm text-red-400">{error}</div>}

            {result && (
              <section className="bg-[#111] border border-white/5 rounded-xl overflow-hidden" aria-label="Install results">
                <div className={`px-6 py-3 text-sm font-medium ${result.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {result.ok ? `Installed release ${result.release}` : "Installation stopped at the first failed step. Fix it and run again — completed steps are kept."}
                </div>
                <ul className="divide-y divide-white/5 text-sm">
                  {result.steps.map((st, i) => (
                    <li key={i} className="px-6 py-3 flex items-center gap-4">
                      <span className="w-24 text-white/50 capitalize">{st.step}</span>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${CHIP[st.status]}`}>{st.status}</span>
                      <span className="text-white/70 truncate" title={st.detail}>{st.detail}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
