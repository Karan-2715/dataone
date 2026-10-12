"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";

/* ───────────── tiny icon set ───────────── */
const Icon = ({ name, size = 16, className = "" }: { name: string; size?: number; className?: string }) => {
  const s: any = { width: size, height: size, className };
  switch (name) {
    case "sparkles": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M12 3v18m9-9H3m14.48-6.36L6.52 17.36m10.96 0L6.52 6.64" /></svg>;
    case "table":    return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>;
    case "alert":    return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>;
    case "arrow-r":  return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>;
    case "send":     return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>;
    case "code":     return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
    case "loader":   return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" className={`${className} animate-spin`}><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>;
    default: return null;
  }
};

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  code?: string;
}

// A pending row-level patch from the chatbot (shown visually before migration)
interface Patch {
  pk_col: string;
  pk_val: any;
  col: string;
  new_val: any;
}

export default function TransformationPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const mappingId = searchParams.get("mappingId");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const [activeTab, setActiveTab] = useState<"clean" | "unclean">("unclean");
  const [messages, setMessages] = useState<Message[]>([
    { id: "1", role: "assistant", content: "Hi! I'm your Data Cleaning Copilot. Describe how you want to transform this data — e.g. \"Replace nulls in status with unknown\", or \"Fill the record with id = 5 name with John\"." }
  ]);
  const [input, setInput] = useState("");
  const [loadingCode, setLoadingCode] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [transformationCode, setTransformationCode] = useState<string>("");

  // Real data from backend
  const [cols, setCols] = useState<string[]>([]);
  const [cleanRows, setCleanRows] = useState<any[][]>([]);
  const [uncleanRows, setUncleanRows] = useState<any[][]>([]);
  const [sourceTable, setSourceTable] = useState<string>("");

  // In-memory patches applied by chatbot, shown as highlights
  const [patches, setPatches] = useState<Patch[]>([]);

  /* ─── fetch real preview from backend ─── */
  const fetchPreview = useCallback(async () => {
    if (!mappingId) return;
    setLoadingPreview(true);
    try {
      const res = await api.get<any>(`/api/v1/transformation/preview/${mappingId}?limit=300`);
      setCols(res.cols || []);
      setCleanRows(res.clean_rows || []);
      setUncleanRows(res.unclean_rows || []);
      setSourceTable(res.source_table || "");
    } catch (e: any) {
      console.error("Preview fetch error:", e);
      // Show friendly error message in chat
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        role: "assistant",
        content: `⚠️ Could not load table preview: ${e?.message || String(e)}`
      }]);
    } finally {
      setLoadingPreview(false);
    }
  }, [mappingId]);

  useEffect(() => { fetchPreview(); }, [fetchPreview]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loadingCode]);

  /* ─── apply patches to a row array ─── */
  const applyPatches = (rows: any[][], currentPatches: Patch[]): any[][] => {
    if (!currentPatches.length || !cols.length) return rows;
    return rows.map(row => {
      const pkColIdx = cols.indexOf(currentPatches[0]?.pk_col);
      const newRow = [...row];
      for (const patch of currentPatches) {
        const pki = cols.indexOf(patch.pk_col);
        const ci  = cols.indexOf(patch.col);
        if (pki === -1 || ci === -1) continue;
        if (String(row[pki]) === String(patch.pk_val)) {
          newRow[ci] = patch.new_val;
        }
      }
      return newRow;
    });
  };

  const displayClean   = applyPatches(cleanRows,   patches);
  const displayUnclean = applyPatches(uncleanRows, patches);

  /* ─── is a cell patched? ─── */
  const isPatchedCell = (row: any[], colIdx: number): boolean => {
    for (const p of patches) {
      const pki = cols.indexOf(p.pk_col);
      const ci  = cols.indexOf(p.col);
      if (pki === -1 || ci === -1) continue;
      if (String(row[pki]) === String(p.pk_val) && ci === colIdx) return true;
    }
    return false;
  };

  /* ─── send chat message ─── */
  const handleSend = async () => {
    if (!input.trim()) return;
    const userMsg: Message = { id: Date.now().toString(), role: "user", content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setLoadingCode(true);

    try {
      const res = await api.post<any>("/api/v1/transformation/generate", {
        prompt: userMsg.content,
        mapping_id: mappingId ? parseInt(mappingId) : null,
        current_code: transformationCode,
        columns: cols,
      });

      const aiMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: res.explanation || "Here is the updated PySpark transformation logic:",
        code: res.code,
      };
      setMessages(prev => [...prev, aiMsg]);

      if (res.code) setTransformationCode(res.code);

      // Apply inline patches immediately so table updates visually
      if (res.patches && res.patches.length > 0) {
        setPatches(prev => [...prev, ...res.patches]);
        setMessages(prev => [...prev, {
          id: (Date.now() + 2).toString(),
          role: "assistant",
          content: `✅ I've highlighted ${res.patches.length} inline change(s) in the table below. These will be committed when you Execute Migration.`
        }]);
      }
    } catch (e: any) {
      setMessages(prev => [...prev, { id: Date.now().toString(), role: "assistant", content: "Error generating transformation: " + (e?.message || String(e)) }]);
    } finally {
      setLoadingCode(false);
    }
  };

  /* ─── execute migration with patches & code ─── */
  const handleExecute = async () => {
    if (!mappingId) return;
    try {
      const run = await api.post<any>(`/api/v1/mappings/${mappingId}/runs`, {
        transformation_code: transformationCode,
        patches,
      });
      if (run?.id) router.push(`/dashboard/migration-status?mappingId=${mappingId}&runId=${run.id}`);
    } catch (e) {
      console.error(e);
      alert("Failed to start migration");
    }
  };

  /* ─── render data table ─── */
  const renderTable = (rows: any[][], showNullHighlight = false) => {
    if (loadingPreview) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-3" style={{ color: "rgba(255,255,255,0.3)" }}>
          <Icon name="loader" size={24} />
          <span className="text-[11px]">Fetching live data from {sourceTable || "source table"}…</span>
        </div>
      );
    }
    if (!cols.length) {
      return (
        <div className="flex-1 flex items-center justify-center text-[11px]" style={{ color: "rgba(255,255,255,0.3)" }}>
          No data loaded — check mapping ID or connection.
        </div>
      );
    }
    if (!rows.length) {
      return (
        <div className="flex-1 flex items-center justify-center text-[11px]" style={{ color: "rgba(255,255,255,0.3)" }}>
          {showNullHighlight ? "🎉 No anomalies found!" : "No clean rows found."}
        </div>
      );
    }

    return (
      <div className="w-full h-full overflow-auto rounded-lg" style={{ border: "1px solid rgba(255,255,255,0.08)", background: "rgba(0,0,0,0.2)" }}>
        <table className="w-full text-left text-[11px] whitespace-nowrap">
          <thead style={{ background: "rgba(255,255,255,0.04)", borderBottom: "1px solid rgba(255,255,255,0.08)", position: "sticky", top: 0, zIndex: 10 }}>
            <tr>
              {cols.map((c, i) => (
                <th key={i} className="px-3 py-2 font-semibold" style={{ color: "rgba(255,255,255,0.6)" }}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                {row.map((val: any, j: number) => {
                  const isNull    = val === null || val === undefined || String(val).trim() === "";
                  const isPatched = isPatchedCell(row, j);
                  return (
                    <td key={j} className="px-3 py-2 font-mono" style={{
                      color: isPatched ? "#4ade80" : isNull ? "#ef4444" : "rgba(255,255,255,0.85)",
                      background: isPatched ? "rgba(74,222,128,0.06)" : "transparent",
                      fontStyle: isPatched ? "italic" : "normal",
                    }}>
                      {isNull ? "NULL" : String(val)}
                      {isPatched && <span className="ml-1 text-[9px] text-green-500 opacity-70">(pending)</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: "#0c0c0c", color: "rgba(255,255,255,0.88)", fontFamily: "'Inter', system-ui, sans-serif" }}>

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", background: "rgba(0,0,0,0.4)" }}>
        <div>
          <h1 className="text-[14px] font-semibold tracking-tight" style={{ color: "rgba(255,255,255,0.92)" }}>Data Transformation &amp; Cleaning</h1>
          <p className="text-[10px] mt-0.5" style={{ color: "rgba(255,255,255,0.4)" }}>
            {sourceTable ? <>Source: <span className="font-mono text-white/50">{sourceTable}</span></> : "Review anomalies and apply AI-generated Spark transformations before migration."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {patches.length > 0 && (
            <span className="text-[10px] px-2.5 py-1 rounded-full font-semibold" style={{ background: "rgba(74,222,128,0.12)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.25)" }}>
              {patches.length} pending edit{patches.length !== 1 ? "s" : ""}
            </span>
          )}
          <button onClick={handleExecute} className="flex items-center gap-1.5 text-[11px] font-bold px-4 py-1.5 rounded-md transition-all shadow-md hover:opacity-90"
            style={{ background: "#ffffff", color: "#000" }}>
            Execute Migration <Icon name="arrow-r" size={11} />
          </button>
        </div>
      </header>

      {/* Main Body */}
      <main className="flex-1 flex min-h-0 overflow-hidden">

        {/* Left Data Preview Panel */}
        <div className="flex-1 flex flex-col min-w-0 p-5 gap-4" style={{ borderRight: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="flex items-center justify-between">
            <div className="flex bg-white/5 rounded-lg p-0.5">
              <button onClick={() => setActiveTab("clean")} className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold rounded-md transition-colors ${activeTab === "clean" ? "bg-white/10 text-white" : "text-white/40 hover:text-white/70"}`}>
                <Icon name="table" size={12} /> Clean ({displayClean.length})
              </button>
              <button onClick={() => setActiveTab("unclean")} className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold rounded-md transition-colors ${activeTab === "unclean" ? "bg-red-500/10 text-red-400" : "text-white/40 hover:text-white/70"}`}>
                <Icon name="alert" size={12} /> Anomalies ({displayUnclean.length})
              </button>
            </div>
            {patches.length > 0 && (
              <button onClick={() => setPatches([])} className="text-[10px] px-2 py-1 rounded text-white/30 hover:text-red-400 transition-colors">
                Clear pending edits
              </button>
            )}
          </div>

          <div className="flex-1 overflow-hidden flex flex-col min-h-0">
            {activeTab === "clean"
              ? renderTable(displayClean, false)
              : renderTable(displayUnclean, true)
            }
          </div>

          {/* PySpark Code Preview */}
          <div className="h-36 flex-shrink-0 flex flex-col rounded-lg overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.1)", background: "rgba(0,0,0,0.5)" }}>
            <div className="px-3 py-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.05)", color: "rgba(255,255,255,0.5)" }}>
              <Icon name="code" size={12} /> PySpark Transformation Pipeline
            </div>
            <div className="flex-1 overflow-auto p-3 text-[11px] font-mono whitespace-pre-wrap" style={{ color: "#a5b4fc" }}>
              {transformationCode || "# No transformations applied yet.\n# Use the chat on the right to instruct the AI."}
            </div>
          </div>
        </div>

        {/* Right AI Chat Panel */}
        <div className="w-80 flex-shrink-0 flex flex-col bg-black/20">
          <div className="px-4 py-3 text-[11px] font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", color: "rgba(255,255,255,0.6)" }}>
            <Icon name="sparkles" size={12} className="text-blue-400" /> Copilot
          </div>
          <div className="flex-1 overflow-auto p-4 space-y-4">
            {messages.map(m => (
              <div key={m.id} className={`flex flex-col gap-1 ${m.role === "user" ? "items-end" : "items-start"}`}>
                <div className={`px-3 py-2 rounded-lg text-[12px] max-w-[90%] leading-relaxed ${m.role === "user" ? "bg-blue-600/20 border border-blue-500/30 text-blue-50" : "bg-white/5 border border-white/10 text-white/80"}`}>
                  {m.content}
                </div>
                {m.code && (
                  <div className="max-w-[90%] mt-1 px-3 py-2 rounded bg-black/60 border border-white/10 text-[10px] font-mono text-blue-300">
                    <pre className="whitespace-pre-wrap break-all">{m.code}</pre>
                  </div>
                )}
              </div>
            ))}
            {loadingCode && (
              <div className="flex gap-1 items-center px-3 py-2 rounded-lg bg-white/5 border border-white/10 w-16">
                <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce" />
                <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce delay-75" />
                <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce delay-150" />
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
          <div className="p-3" style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}>
            <div className="relative flex items-center">
              <input
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleSend()}
                placeholder="e.g. Fill id=5 name with John, or replace nulls…"
                className="w-full bg-white/5 border border-white/10 rounded-lg pl-3 pr-10 py-2.5 text-[12px] text-white focus:outline-none focus:border-blue-500/50"
              />
              <button onClick={handleSend} disabled={!input.trim()} className="absolute right-2 p-1.5 rounded-md text-white/40 hover:text-white/90 disabled:opacity-30 transition-opacity">
                <Icon name="send" size={14} />
              </button>
            </div>
          </div>
        </div>

      </main>
    </div>
  );
}
