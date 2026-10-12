"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { UseWidgetDataResult } from "../hooks/useWidgetData";
import type { DQScore, DQSummary } from "../types";

type Tone = { text: string; bar: string; chip: string; stroke: string };

const TONES: Record<"good" | "warn" | "bad" | "none", Tone> = {
  good: { text: "text-emerald-400", bar: "bg-emerald-400", chip: "bg-emerald-400/10 text-emerald-400", stroke: "#34d399" },
  warn: { text: "text-amber-400", bar: "bg-amber-400", chip: "bg-amber-400/10 text-amber-400", stroke: "#fbbf24" },
  bad: { text: "text-red-400", bar: "bg-red-400", chip: "bg-red-400/10 text-red-400", stroke: "#f87171" },
  none: { text: "text-white/40", bar: "bg-white/20", chip: "bg-white/5 text-white/40", stroke: "#71717a" },
};

export function scoreTone(score: DQScore): Tone {
  if (score === null || score === undefined) return TONES.none;
  if (score >= 90) return TONES.good;
  if (score >= 70) return TONES.warn;
  return TONES.bad;
}

export function formatScore(score: DQScore): string {
  return score === null || score === undefined ? "—" : `${score.toFixed(1)}%`;
}

function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

const DIMENSIONS = [
  { key: "completeness", label: "Completeness" },
  { key: "uniqueness", label: "Uniqueness" },
  { key: "validity", label: "Validity" },
  { key: "freshness", label: "Freshness" },
] as const;

function ScoreCell({ score }: { score: DQScore }) {
  return <span className={scoreTone(score).text}>{formatScore(score)}</span>;
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[#111] border border-white/5 rounded-xl px-6 py-12 text-center text-sm text-white/50">{children}</div>
  );
}

type FileRunState = { finished: boolean; success: boolean; message: string };
type FileSyncResult = { synced: boolean; tables: number; detail?: string };

const FILE_RUN_POLL_MS = 5_000;
const FILE_RUN_MAX_POLLS = 120; // ~10 minutes

/** Runs the Databricks file data-quality job (profiles every file in the uploads volume), then pulls its results. */
function FileCheckControls({ onDone }: { onDone: () => void }) {
  const [phase, setPhase] = useState<"idle" | "running" | "syncing">("idle");
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const describe = (err: unknown) => (err instanceof ApiError || err instanceof Error ? err.message : "Request failed");

  const sync = async () => {
    setPhase("syncing");
    try {
      const res = await api.post<FileSyncResult>("/api/v1/data-quality/files/sync", {});
      if (!mounted.current) return;
      setMessage({ text: res.synced ? `Synced ${res.tables} ${res.tables === 1 ? "file" : "files"}` : res.detail ?? "No files profiled yet", error: false });
      onDone();
    } catch (err) {
      if (mounted.current) setMessage({ text: describe(err), error: true });
    } finally {
      if (mounted.current) setPhase("idle");
    }
  };

  const run = async () => {
    setMessage(null);
    setPhase("running");
    try {
      const { run_id } = await api.post<{ run_id: number }>("/api/v1/data-quality/files/run", {});
      for (let i = 0; i < FILE_RUN_MAX_POLLS && mounted.current; i++) {
        await new Promise((r) => setTimeout(r, FILE_RUN_POLL_MS));
        const state = await api.get<FileRunState>(`/api/v1/data-quality/files/runs/${run_id}`);
        if (!state.finished) continue;
        if (!state.success) throw new Error(state.message || "The file quality job failed");
        await sync();
        return;
      }
      if (mounted.current) setMessage({ text: "The job is still running — use Sync results when it finishes", error: false });
    } catch (err) {
      if (mounted.current) setMessage({ text: describe(err), error: true });
    }
    if (mounted.current) setPhase("idle");
  };

  const busy = phase !== "idle";
  return (
    <div className="flex flex-wrap items-center gap-2">
      {message && <span className={`text-xs ${message.error ? "text-red-400" : "text-white/50"}`} role="status">{message.text}</span>}
      <button
        onClick={sync} disabled={busy}
        className="px-3 py-1.5 rounded-lg border border-white/10 text-xs text-white/70 hover:bg-white/5 disabled:opacity-50 transition-colors">
        {phase === "syncing" ? "Syncing…" : "Sync results"}
      </button>
      <button
        onClick={run} disabled={busy}
        title="Profile every file in the Databricks uploads volume (csv, json, parquet, …)"
        className="px-3 py-1.5 rounded-lg bg-[#3b82f6] text-white text-xs font-semibold hover:bg-[#2563eb] disabled:opacity-50 transition-colors">
        {phase === "running" ? "Checking files…" : "Check uploaded files"}
      </button>
    </div>
  );
}

type Scope = "all" | "files" | "tables";
const SCOPE_STORAGE_KEY = "dataone.dq.scope";
const SCOPES: { key: Scope; label: string }[] = [
  { key: "all", label: "All datasets" },
  { key: "files", label: "Uploaded files" },
  { key: "tables", label: "Tables" },
];

function ScopeSwitch({ scope, counts, onChange }: { scope: Scope; counts: Record<string, number>; onChange: (s: Scope) => void }) {
  return (
    <div role="radiogroup" aria-label="Data quality scope" className="inline-flex rounded-lg border border-white/10 overflow-hidden text-xs">
      {SCOPES.map((s) => (
        <button
          key={s.key} role="radio" aria-checked={scope === s.key} onClick={() => onChange(s.key)}
          className={`px-3 py-1.5 transition-colors ${scope === s.key ? "bg-white/10 text-white" : "text-white/50 hover:bg-white/5"}`}>
          {s.label} <span className="text-white/40">({counts[s.key] ?? 0})</span>
        </button>
      ))}
    </div>
  );
}

export function DataQualitySection({ widget }: { widget: UseWidgetDataResult<DQSummary> }) {
  const { data, isLoading, isError, errorMessage, refetch } = widget;

  // The dashboard averages every dataset, which blurs a single uploaded file. The switch narrows the view to
  // uploaded files or warehouse tables; "all" keeps using the page's own summary request.
  const [scope, setScope] = useState<Scope>("all");
  const [scoped, setScoped] = useState<DQSummary | null>(null);
  const [scopedLoading, setScopedLoading] = useState(false);
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(SCOPE_STORAGE_KEY);
      if (stored === "files" || stored === "tables") setScope(stored);
    } catch { /* storage unavailable: stay on "all" */ }
  }, []);
  const chooseScope = (next: Scope) => {
    setScope(next);
    try { window.localStorage.setItem(SCOPE_STORAGE_KEY, next); } catch { /* ignore */ }
  };
  useEffect(() => {
    if (scope === "all") { setScoped(null); return; }
    let cancelled = false;
    setScopedLoading(true);
    api.get<DQSummary>(`/api/v1/data-quality/summary?table_limit=100&scope=${scope}`)
      .then((res) => { if (!cancelled) setScoped(res); })
      .catch(() => { if (!cancelled) setScoped(null); })
      .finally(() => { if (!cancelled) setScopedLoading(false); });
    return () => { cancelled = true; };
  }, [scope, data]); // `data` changes after a sync / refetch, which refreshes the scoped view too

  const view = scope === "all" ? data : scoped;
  const latest = view?.latest ?? null;
  const counts = data?.scope_counts;

  let body: React.ReactNode;
  if ((isLoading && !data) || (scope !== "all" && scopedLoading && !scoped)) {
    body = <div className="bg-[#111] border border-white/5 rounded-xl h-64 animate-pulse" role="status" aria-label="Loading data quality" />;
  } else if (isError && !data) {
    body = (
      <Placeholder>
        <p className="text-red-400">Couldn&apos;t load data quality{errorMessage ? `: ${errorMessage}` : ""}</p>
        <button onClick={refetch} className="mt-3 text-[#3b82f6] hover:underline">Retry</button>
      </Placeholder>
    );
  } else if (!latest && scope !== "all") {
    body = (
      <Placeholder>
        <p className="text-white/70 font-medium">{scope === "files" ? "No uploaded-file results yet" : "No table results yet"}</p>
        <p className="mt-1">
          {scope === "files"
            ? "Upload a file on the Connectors page, then click Check uploaded files."
            : "Run the table data quality job from workspace setup."}
        </p>
      </Placeholder>
    );
  } else if (!latest) {
    body = (
      <Placeholder>
        <p className="text-white/70 font-medium">No data quality results yet</p>
        <p className="mt-1">
          {data?.ingest_configured === false
            ? "The backend has no DQ_INGEST_KEY configured, so Databricks results can't be received yet."
            : "Install DataOne in your Databricks workspace and run the data quality job; the scores will appear here."}
        </p>
        <a href="/dashboard/workspace-setup" className="inline-block mt-3 text-[#3b82f6] hover:underline">Open workspace setup</a>
      </Placeholder>
    );
  } else {
    body = (
      <div className="flex flex-col gap-6">
        <div className="border border-white/10 rounded-lg overflow-x-auto bg-[#111]">
          <table className="w-full text-left text-sm">
            <thead className="bg-white/5 text-white/50 text-xs">
              <tr>
                <th className="px-4 py-3 font-medium">Dataset</th>
                <th className="px-4 py-3 font-medium text-right">Rows</th>
                {DIMENSIONS.map((d) => <th key={d.key} className="px-4 py-3 font-medium text-right">{d.label}</th>)}
                <th className="px-4 py-3 font-medium text-right">Score</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-white/80">
              {view!.tables.map((t) => (
                <tr key={t.table_name} className="hover:bg-white/5">
                  <td className="px-4 py-3 max-w-xs">
                    <div className="text-[#3b82f6] font-medium truncate" title={t.table_name}>{t.table_name}</div>
                    {t.issues[0] && <div className="text-xs text-white/40 truncate" title={t.issues[0].detail}>{t.issues[0].column ? `${t.issues[0].column}: ` : ""}{t.issues[0].detail}</div>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{t.row_count === null ? "—" : t.row_count.toLocaleString()}</td>
                  {DIMENSIONS.map((d) => <td key={d.key} className="px-4 py-3 text-right tabular-nums"><ScoreCell score={t[d.key]} /></td>)}
                  <td className="px-4 py-3 text-right">
                    <span className={`px-2 py-1 rounded text-xs font-medium tabular-nums ${scoreTone(t.overall_score).chip}`}>{formatScore(t.overall_score)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <section className="mb-12" aria-labelledby="dq-heading">
      <div className="flex flex-wrap items-end justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="m9 12 2 2 4-4" /></svg>
          <h2 id="dq-heading" className="text-xl font-bold text-white tracking-tight">Data quality</h2>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          {counts && (counts.files > 0 && counts.tables > 0 || scope !== "all") && (
            <ScopeSwitch scope={scope} counts={counts} onChange={chooseScope} />
          )}
          {latest && (
            <span className="text-xs text-white/40">
              {latest.table_count} {latest.table_count === 1 ? "dataset" : "datasets"} · {latest.catalog ?? "Databricks"} · profiled {timeAgo(latest.completed_at)}
            </span>
          )}
          <FileCheckControls onDone={refetch} />
        </div>
      </div>
      <p className="text-white/50 text-sm mb-6">Completeness, uniqueness, validity and freshness of your Databricks datasets and uploaded files (csv, json, parquet, …), Scores are 0-100; "—" means not measured. Lowest-scoring datasets first.</p>
      {body}
    </section>
  );
}
