"use client";

import { useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";

// ─── Minimal Icons (no emojis, pure SVG geometry) ──────────────────────────
const Icon = ({ name, size = 16, className = "", style }: { name: string; size?: number; className?: string; style?: React.CSSProperties }) => {
  const s = { width: size, height: size, className, style };
  switch (name) {
    case "check": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="M20 6 9 17l-5-5"/></svg>;
    case "x-circle": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>;
    case "refresh": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8M3 16l2.3 2.7A9 9 0 0 0 21 12"/></svg>;
    case "arrow-l": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M19 12H5m5 5-5-5 5-5"/></svg>;
    case "loader": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48 2.83-2.83"/></svg>;
    default: return null;
  }
};

export default function MigrationRunStatusPage() {
  const searchParams = useSearchParams();
  const mappingId = searchParams.get("mappingId");
  const runId = searchParams.get("runId");
  const router = useRouter();
  const [run, setRun] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!mappingId || !runId) return;

    const fetchStatus = async () => {
      try {
        const res = await api.get<any>(`/api/v1/mappings/${mappingId}/runs/${runId}`);
        setRun(res);
        setLoading(false);
        if (res.state === 'succeeded' || res.state === 'failed') {
          return true; // stop polling
        }
      } catch (err) {
        console.error("Failed to fetch run status", err);
      }
      return false;
    };

    fetchStatus();

    const interval = setInterval(async () => {
      const isTerminal = await fetchStatus();
      if (isTerminal) {
        clearInterval(interval);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [mappingId, runId]);

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: "#0c0c0c", color: "rgba(255,255,255,0.88)", fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* ── Topbar ── */}
      <header className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", background: "rgba(0,0,0,0.4)" }}>
        <div className="flex items-center gap-4">
          <button onClick={() => router.back()} className="flex items-center gap-1.5 text-[11px] font-bold px-3 py-1.5 rounded-md transition-all duration-150 shadow-md hover:bg-white/10"
            style={{ color: "rgba(255,255,255,0.7)", border: "1px solid rgba(255,255,255,0.1)" }}>
            <Icon name="arrow-l" size={12} /> Back
          </button>
          <div>
            <h1 className="text-[14px] font-semibold tracking-tight leading-none" style={{ color: "rgba(255,255,255,0.9)", letterSpacing: "-0.01em" }}>Migration Execution Status</h1>
            <p className="text-[11px] mt-1" style={{ color: "rgba(255,255,255,0.4)" }}>Mapping ID: {mappingId} | Run ID: {runId}</p>
          </div>
        </div>
      </header>

      {/* ── Body ── */}
      <main className="flex-1 overflow-auto p-6 flex items-center justify-center">
        {loading ? (
          <div className="flex flex-col items-center gap-4">
            <div className="animate-spin text-emerald-400">
               <Icon name="loader" size={32} />
            </div>
            <p className="text-sm font-medium" style={{ color: "rgba(255,255,255,0.6)" }}>Loading migration status...</p>
          </div>
        ) : run ? (
          <div className="w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl relative" style={{ border: "1px solid rgba(255,255,255,0.1)", background: "rgba(20,20,22,0.95)" }}>
            {/* Status Header */}
            <div className="px-8 py-6 flex items-center justify-between" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)", background: run.state === 'succeeded' ? "rgba(52,211,153,0.05)" : run.state === 'failed' ? "rgba(239,68,68,0.05)" : "rgba(59,130,246,0.05)" }}>
              <div className="flex items-center gap-4">
                {run.state === 'succeeded' ? (
                  <div className="w-12 h-12 rounded-full flex items-center justify-center bg-emerald-500/20 text-emerald-400 shadow-[0_0_20px_rgba(52,211,153,0.2)]">
                    <Icon name="check" size={24} />
                  </div>
                ) : run.state === 'failed' ? (
                  <div className="w-12 h-12 rounded-full flex items-center justify-center bg-red-500/20 text-red-400 shadow-[0_0_20px_rgba(239,68,68,0.2)]">
                    <Icon name="x-circle" size={24} />
                  </div>
                ) : (
                  <div className="w-12 h-12 rounded-full flex items-center justify-center bg-blue-500/20 text-blue-400 shadow-[0_0_20px_rgba(59,130,246,0.2)] relative">
                    <span className="absolute inset-0 rounded-full border-2 border-blue-400/30 border-t-blue-400 animate-spin" />
                    <Icon name="loader" size={20} className="animate-pulse" />
                  </div>
                )}
                <div>
                  <h2 className="text-xl font-bold capitalize" style={{ color: run.state === 'succeeded' ? "#34d399" : run.state === 'failed' ? "#ef4444" : "#60a5fa" }}>
                    {run.state.replace('_', ' ')}
                  </h2>
                  <p className="text-xs mt-1" style={{ color: "rgba(255,255,255,0.5)" }}>Databricks Job Execution</p>
                </div>
              </div>
            </div>

            {/* Metrics */}
            <div className="grid grid-cols-2 gap-px" style={{ background: "rgba(255,255,255,0.05)" }}>
              <div className="px-8 py-6" style={{ background: "rgba(20,20,22,1)" }}>
                <p className="text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: "rgba(255,255,255,0.4)" }}>Rows Read</p>
                <p className="text-3xl font-mono tracking-tight" style={{ color: "rgba(255,255,255,0.9)" }}>
                  {run.rows_read !== null ? run.rows_read.toLocaleString() : "—"}
                </p>
              </div>
              <div className="px-8 py-6" style={{ background: "rgba(20,20,22,1)" }}>
                <p className="text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: "rgba(255,255,255,0.4)" }}>Rows Written</p>
                <p className="text-3xl font-mono tracking-tight" style={{ color: "rgba(255,255,255,0.9)" }}>
                  {run.rows_written !== null ? run.rows_written.toLocaleString() : "—"}
                </p>
              </div>
            </div>

            {/* Error Message if failed */}
            {run.state === 'failed' && run.error && (
              <div className="px-8 py-6" style={{ borderTop: "1px solid rgba(255,255,255,0.08)", background: "rgba(239,68,68,0.02)" }}>
                <p className="text-[10px] font-bold uppercase tracking-wider text-red-400 mb-2">Error Details</p>
                <div className="p-4 rounded-lg bg-black/40 border border-red-500/20 text-red-300 text-xs font-medium break-words whitespace-pre-wrap">
                  {(() => {
                    if (run.error.includes("duplicate key value violates unique constraint") || run.error.includes("already exists")) {
                      return "Migration failed: The data you are trying to load already exists in the target table.";
                    }
                    const causedBy = run.error.split("Caused by: ")[1];
                    if (causedBy) {
                      return "Migration failed: " + causedBy.split("\n")[0];
                    }
                    return "Migration failed: " + run.error.split("\n")[0];
                  })()}
                </div>
              </div>
            )}
            
            {/* Run details */}
            <div className="px-8 py-5 flex items-center justify-between text-xs" style={{ borderTop: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.01)" }}>
               <div style={{ color: "rgba(255,255,255,0.4)" }}>Started at: <span className="text-white/70 ml-1">{new Date(run.started_at).toLocaleString()}</span></div>
               {run.finished_at && <div style={{ color: "rgba(255,255,255,0.4)" }}>Finished at: <span className="text-white/70 ml-1">{new Date(run.finished_at).toLocaleString()}</span></div>}
            </div>
          </div>
        ) : (
          <div className="text-white/50 text-sm">Failed to load run data.</div>
        )}
      </main>
    </div>
  );
}
