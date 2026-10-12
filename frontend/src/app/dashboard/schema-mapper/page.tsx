"use client";

import { useEffect, useState, useMemo, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import ReactFlow, { Background, Controls, Handle, Position, MarkerType, BackgroundVariant } from 'reactflow';
import 'reactflow/dist/style.css';

// ─── Minimal Icons (no emojis, pure SVG geometry) ──────────────────────────
const Icon = ({ name, size = 16, className = "", style }: { name: string; size?: number; className?: string; style?: React.CSSProperties }) => {
  const s = { width: size, height: size, className, style };
  switch (name) {
    case "arrows": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M7 16V4m0 0L3 8m4-4 4 4M17 8v12m0 0 4-4m-4 4-4-4" /></svg>;
    case "grid": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>;
    case "db": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>;
    case "table": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>;
    case "arrow-r": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>;
    case "chevron-d": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="m6 9 6 6 6-6"/></svg>;
    case "check": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="M20 6 9 17l-5-5"/></svg>;
    case "x-circle": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>;
    case "info": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>;
    case "refresh": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8M3 16l2.3 2.7A9 9 0 0 0 21 12"/></svg>;
    case "key": return <svg {...s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><circle cx="7.5" cy="15.5" r="4.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3"/></svg>;
    default: return null;
  }
};

// ─── Types ───────────────────────────────────────────────────────────────────
interface Col { id: number; column_name: string; data_type: string; is_primary_key: boolean; }
interface Tbl { id: number; table_name: string; columns: Col[]; }

// ─── TypePill ────────────────────────────────────────────────────────────────
const TypePill = ({ type }: { type?: string }) => {
  if (!type) return null;
  return (
    <span className="inline-block text-[9px] font-mono font-semibold px-1.5 py-0.5 rounded-sm flex-shrink-0"
      style={{ background: "rgba(255,255,255,0.06)", color: "rgba(255,255,255,0.45)", border: "1px solid rgba(255,255,255,0.08)", letterSpacing: "0.04em" }}>
      {type}
    </span>
  );
};

// ─── Styled Select ───────────────────────────────────────────────────────────
const Field = ({ label, value, onChange, disabled, options, placeholder }: {
  label: string; value: string; onChange: (v: string) => void;
  disabled?: boolean; options: string[]; placeholder: string;
}) => (
  <div>
    <label className="block text-[10px] font-semibold uppercase tracking-[0.12em] mb-2" style={{ color: "rgba(255,255,255,0.3)" }}>{label}</label>
    <div className="relative">
      <select value={value} onChange={e => onChange(e.target.value)} disabled={disabled}
        className="w-full appearance-none rounded-lg px-3 py-2.5 text-[13px] pr-8 transition-all duration-200 focus:outline-none disabled:cursor-not-allowed"
        style={{
          background: "rgba(255,255,255,0.03)",
          border: `1px solid ${value ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.08)"}`,
          color: value ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.3)",
          opacity: disabled ? 0.35 : 1,
        }}>
        <option value="">{placeholder}</option>
        {options.map(o => <option key={o} value={o} style={{ background: "#111" }}>{o}</option>)}
      </select>
      <span className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "rgba(255,255,255,0.25)" }}>
        <Icon name="chevron-d" size={12} />
      </span>
    </div>
  </div>
);

// ─── Confidence bar ───────────────────────────────────────────────────────────
const Bar = ({ pct }: { pct: number }) => (
  <div className="w-full h-px rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.06)" }}>
    <div className="h-full rounded-full transition-all duration-700 ease-out" style={{ width: `${pct}%`, background: pct >= 80 ? "rgba(255,255,255,0.7)" : pct >= 60 ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.25)" }} />
  </div>
);

// ─── Animated scan line (loading) ─────────────────────────────────────────────
const ScanLine = () => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let pos = 0; let dir = 1;
    const id = setInterval(() => {
      if (!ref.current) return;
      pos += dir * 0.8;
      if (pos >= 100) dir = -1;
      if (pos <= 0) dir = 1;
      ref.current.style.top = pos + "%";
    }, 16);
    return () => clearInterval(id);
  }, []);
  return (
    <div ref={ref} className="absolute left-0 right-0 pointer-events-none transition-none" style={{ height: 1, background: "linear-gradient(90deg,transparent,rgba(255,255,255,0.4),transparent)" }} />
  );
};

// ─── Main ─────────────────────────────────────────────────────────────────────

// ─── ERD Node ────────────────────────────────────────────────────────────────
const TableNode = ({ data }: any) => {
  const { title, columns, isSource } = data;
  return (
    <div className="rounded-xl overflow-hidden shadow-2xl" style={{ border: `1px solid ${isSource ? 'rgba(59,130,246,0.3)' : 'rgba(52,211,153,0.3)'}`, background: '#0a0a0f', width: 240 }}>
      <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-widest flex items-center gap-2" style={{ background: isSource ? 'rgba(59,130,246,0.08)' : 'rgba(52,211,153,0.08)', color: isSource ? '#60a5fa' : '#34d399' }}>
        <Icon name={isSource ? "db" : "table"} size={12} />
        {title}
      </div>
      <div className="py-1 flex flex-col gap-px" style={{ background: 'rgba(0,0,0,0.4)' }}>
        {columns.map((col: any) => (
          <div key={col.id} className="relative px-3 py-1.5 flex justify-between items-center hover:bg-white/[0.03] transition-colors group">
            {!isSource && <Handle type="target" position={Position.Left} id={col.name} className="!w-1.5 !h-1.5 !bg-emerald-400 !border-none !-left-1 opacity-0 group-hover:opacity-100 transition-opacity" />}
            <span className="text-[10px] font-mono text-white/80">{col.name}</span>
            <span className="text-[8px] text-white/30 ml-2">{col.type}</span>
            {isSource && <Handle type="source" position={Position.Right} id={col.name} className="!w-1.5 !h-1.5 !bg-blue-400 !border-none !-right-1 opacity-0 group-hover:opacity-100 transition-opacity" />}
          </div>
        ))}
      </div>
    </div>
  );
};
const nodeTypes = { tableNode: TableNode };

export default function SchemaMapperPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const connId = searchParams.get("connection_id");

  const [sparkConnId, setSparkConnId] = useState<number | null>(null);
  const [tables, setTables] = useState<Tbl[]>([]);
  const [srcCat, setSrcCat] = useState(""); const [srcSch, setSrcSch] = useState(""); const [srcTbl, setSrcTbl] = useState("");
  const [tgtCat, setTgtCat] = useState(""); const [tgtSch, setTgtSch] = useState("");
  const [mode, setMode] = useState<"cfg" | "run">("cfg");
  const [activeTab, setActiveTab] = useState<"list" | "erd">("list");
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [mappingId, setMappingId] = useState<number | null>(null);
  const [migrationRun, setMigrationRun] = useState<any>(null);
  const [initialized, setInitialized] = useState(false);
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    // Always start fresh — clear any stale cached state from previous sessions.
    localStorage.removeItem("schema_mapper_state");
    setInitialized(true);
  }, []);

  useEffect(() => {
    if (mode === "cfg") {
      api.get<any>("/api/v1/mappings/").then(res => {
        setHistory(res.items || []);
      }).catch(err => console.error("Failed to fetch history", err));
    }
  }, [mode]);

  const resumeJob = async (job: any) => {
    const match = job.name.match(/Map (.*?) → (.*)/);
    if (match) {
      const srcParts = match[1].split(".");
      if (srcParts.length === 3) {
        setSrcCat(srcParts[0]); setSrcSch(srcParts[1]); setSrcTbl(srcParts[2]);
      } else if (srcParts.length === 2) {
        setSrcCat("default"); setSrcSch(srcParts[0]); setSrcTbl(srcParts[1]);
      } else {
        setSrcCat("default"); setSrcSch("default"); setSrcTbl(srcParts[0]);
      }
      const tgtParts = match[2].split(".");
      if (tgtParts.length === 2) {
        setTgtCat(tgtParts[0]); setTgtSch(tgtParts[1]);
      } else {
        setTgtCat("default"); setTgtSch(tgtParts[0]);
      }
    }
    
    setMappingId(job.id);
    setMode("run");
    setLoading(true);
    setSuggestions([]);
    try {
      const r = await api.get<any>(`/api/v1/mappings/${job.id}/suggestions?limit=200`);
      if (r.items?.length > 0) {
        setSuggestions(r.items.sort((a: any, b: any) => b.confidence - a.confidence));
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (connId) { setSparkConnId(+connId); return; }
    api.get<any[]>("/api/v1/connectors/").then(res => {
      const list = Array.isArray(res) ? res : (res as any).connectors ?? [];
      const db = list.find((c: any) => c.type?.toLowerCase() === "databricks");
      if (db) setSparkConnId(db.id);
    });
  }, [connId]);

  useEffect(() => {
    if (!sparkConnId) return;
    api.get<{ schema: Record<string, any[]> }>(`/api/v1/connectors/${sparkConnId}/schema`).then(res => {
      let uid = 1;
      setTables(Object.entries(res.schema ?? {}).map(([name, cols]) => ({
        id: uid++, table_name: name,
        columns: (cols || []).map(c => ({ id: uid++, column_name: c.name, data_type: c.type ?? "", is_primary_key: c.primary_key ?? false })),
      })));
    });
  }, [sparkConnId]);

  const struct = useMemo(() => {
    const s: Record<string, Record<string, string[]>> = {};
    tables.forEach(t => {
      const p = t.table_name.split(".");
      const [cat, sch, tbl] = p.length >= 3 ? [p[0], p[1], p.slice(2).join(".")] : p.length === 2 ? ["default", p[0], p[1]] : ["default", "default", t.table_name];
      if (!s[cat]) s[cat] = {};
      if (!s[cat][sch]) s[cat][sch] = [];
      s[cat][sch].push(tbl);
    });
    return s;
  }, [tables]);

  const cats = Object.keys(struct).sort();
  const srcSchs = srcCat ? Object.keys(struct[srcCat] || {}).sort() : [];
  const srcTbls = srcSch ? (struct[srcCat]?.[srcSch] || []).sort() : [];
  const tgtSchs = tgtCat ? Object.keys(struct[tgtCat] || {}).sort() : [];
  const tgtTbls = useMemo(() => tgtCat && tgtSch ? tables.filter(t => {
    const p = t.table_name.split(".");
    const [c, s] = p.length >= 3 ? [p[0], p[1]] : p.length === 2 ? ["default", p[0]] : ["default", "default"];
    return c === tgtCat && s === tgtSch;
  }) : [], [tables, tgtCat, tgtSch]);
  const srcObj = useMemo(() => tables.find(t => {
    const p = t.table_name.split(".");
    const [c, s, tb] = p.length >= 3 ? [p[0], p[1], p.slice(2).join(".")] : p.length === 2 ? ["default", p[0], p[1]] : ["default", "default", t.table_name];
    return c === srcCat && s === srcSch && tb === srcTbl;
  }), [tables, srcCat, srcSch, srcTbl]);
  const ready = !!srcTbl && !!tgtSch && !!sparkConnId;

  const run = async () => {
    if (!ready || !srcObj) return;
    setMode("run"); setLoading(true); setSuggestions([]);
    const fullSrc = srcObj.table_name;
    const fullTgt = `${tgtCat}.${tgtSch}`;
    try {
      const m = await api.post<any>("/api/v1/mappings/", { name: `Map ${fullSrc} \u2192 ${fullTgt}`, source_id: sparkConnId, target_id: sparkConnId });
      setMappingId(m.id);
      await api.post(`/api/v1/mappings/${m.id}/suggestions`, {});
      const poll = setInterval(async () => {
        const r = await api.get<any>(`/api/v1/mappings/${m.id}/suggestions?limit=200`);
        if (r.items?.length > 0) {
          clearInterval(poll);
          const err = r.items.find((i: any) => i.status === "error" || i.target_table === "ERROR");
          if (err) { setSuggestions([err]); setLoading(false); return; }
          setSuggestions(r.items.filter((i: any) => i.source_table === fullSrc).sort((a: any, b: any) => b.confidence - a.confidence));
          setLoading(false);
        }
      }, 4000);
      setTimeout(() => { clearInterval(poll); setLoading(false); }, 180000);
    } catch { setLoading(false); }
  };

  const reset = () => { 
    setMode("cfg"); setSuggestions([]); setMappingId(null); setMigrationRun(null); 
    localStorage.removeItem("schema_mapper_state");
  };

  const triggerMigration = async () => {
    if (!mappingId) return;
    try {
      const run = await api.post<any>(`/api/v1/mappings/${mappingId}/runs`, {});
      setMigrationRun(run);
      
      // Redirect to the newly created migration status page
      if (run && run.id) {
         router.push(`/dashboard/migration-status?mappingId=${mappingId}&runId=${run.id}`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const erdNodes = useMemo(() => {
    if (!srcObj) return [];
    const nodes = [];
    nodes.push({
      id: "src", type: "tableNode", position: { x: 50, y: 150 },
      data: { title: srcObj.table_name.split(".").pop(), isSource: true, columns: srcObj.columns.map(c => ({ id: c.id, name: c.column_name, type: c.data_type })) }
    });
    tgtTbls.forEach((t, i) => {
      nodes.push({
        id: `tgt-${t.table_name}`, type: "tableNode", position: { x: 500, y: 50 + i * 250 },
        data: { title: t.table_name.split(".").pop(), isSource: false, columns: t.columns.map(c => ({ id: c.id, name: c.column_name, type: c.data_type })) }
      });
    });
    return nodes;
  }, [srcObj, tgtTbls]);

  const erdEdges = useMemo(() => {
    if (!suggestions || suggestions.length === 0 || suggestions[0].status === "error") return [];
    return suggestions.map((s, i) => {
      const pct = s.confidence || 0;
      const color = pct >= 80 ? "rgba(255,255,255,0.7)" : pct >= 60 ? "rgba(255,255,255,0.4)" : "rgba(255,255,255,0.2)";
      return {
        id: `edge-${i}`, source: "src", sourceHandle: s.source_column,
        target: `tgt-${s.target_table}`, targetHandle: s.target_column,
        animated: true,
        style: { stroke: color, strokeWidth: 1.5, strokeDasharray: "4 4" },
        markerEnd: { type: MarkerType.ArrowClosed, color: color, width: 20, height: 20 }
      };
    });
  }, [suggestions]);


  // ─── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: "#0c0c0c", color: "rgba(255,255,255,0.88)", fontFamily: "'Inter', system-ui, sans-serif" }}>

      {/* ── Topbar ── */}
      <header className="flex items-center justify-between px-6 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", background: "rgba(0,0,0,0.4)" }}>
        <div className="flex items-center gap-3">
          {/* Geometric logo mark */}
          <div className="relative w-7 h-7 flex-shrink-0">
            <div className="absolute inset-0 border border-white/20 rounded-sm rotate-45 scale-75" />
            <div className="absolute inset-0 border border-white/60 rounded-sm" />
            <Icon name="arrows" size={11} className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
          </div>
          <div>
            <h1 className="text-[13px] font-semibold tracking-tight leading-none" style={{ color: "rgba(255,255,255,0.9)", letterSpacing: "-0.01em" }}>ReMatch Schema Engine</h1>
            <p className="text-[10px] mt-0.5" style={{ color: "rgba(255,255,255,0.28)" }}>Semantic field alignment via vector embeddings</p>
          </div>
        </div>
        {mode === "run" && (
          <div className="flex items-center gap-2">
            {mappingId && !loading && suggestions.length > 0 && !migrationRun && (
               <>
                 <button onClick={async () => {
                    try {
                      await api.post(`/api/v1/mappings/${mappingId}/publish`, {});
                      alert("Mapping published successfully!");
                    } catch (e: any) {
                      alert("Publish failed: " + (e.message || String(e)));
                    }
                 }} className="flex items-center gap-1.5 text-[11px] font-bold px-4 py-1.5 rounded-md transition-all duration-150 shadow-md"
                    style={{ background: "#3b82f6", color: "#fff" }}>
                   <Icon name="check" size={11} /> Publish
                 </button>
                 <button onClick={() => router.push(`/dashboard/transformation?mappingId=${mappingId}`)} className="flex items-center gap-1.5 text-[11px] font-bold px-4 py-1.5 rounded-md transition-all duration-150 shadow-md"
                    style={{ background: "#ffffff", color: "#000" }}>
                   Next: Transformation & Cleaning <Icon name="arrow-r" size={11} />
                 </button>
               </>
            )}
            {migrationRun && (
               <div className="flex items-center gap-2 text-[10px] px-3 py-1.5 rounded-md" style={{ background: "rgba(255,255,255,0.1)", color: "#fff" }}>
                 <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                 Migration {migrationRun.state.toUpperCase()} (Run #{migrationRun.id})
               </div>
            )}
            <button onClick={reset} className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-md transition-all duration-150 hover:bg-white/5"
              style={{ color: "rgba(255,255,255,0.35)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <Icon name="refresh" size={11} /> Reset
            </button>
          </div>
        )}
      </header>

      {/* ── Body ── */}
      <main className="flex-1 overflow-hidden p-4">

        {/* ═══ CONFIGURATION MODE ═══════════════════════════════════════════ */}
        {mode === "cfg" && (
          <div className="h-full flex flex-col gap-4 overflow-y-auto pb-12 pr-2">
            <div className="text-center py-2 flex-shrink-0">
              <h2 className="text-[22px] font-bold tracking-tight" style={{ color: "rgba(255,255,255,0.92)", letterSpacing: "-0.02em" }}>Configure Mapping</h2>
              <p className="text-sm mt-1" style={{ color: "rgba(255,255,255,0.28)" }}>Define source and target — the engine will compute semantic similarity across all field pairs.</p>
            </div>

            <div className="flex gap-3 h-[450px] flex-shrink-0">
              {/* SOURCE */}
              <Panel label="Source" sub="From · read" icon="db">
                <Field label="Catalog" value={srcCat} onChange={v => { setSrcCat(v); setSrcSch(""); setSrcTbl(""); }} options={cats} placeholder="Select catalog" />
                <Field label="Schema" value={srcSch} onChange={v => { setSrcSch(v); setSrcTbl(""); }} disabled={!srcCat} options={srcSchs} placeholder="Select schema" />
                <Field label="Table" value={srcTbl} onChange={setSrcTbl} disabled={!srcSch} options={srcTbls} placeholder="Select table" />
                {srcObj ? (
                  <ColList cols={srcObj.columns} count={srcObj.columns.length} label="Fields" />
                ) : (
                  <EmptyHint icon="table" text="Select a table to inspect its fields" />
                )}
              </Panel>

              {/* CENTER */}
              <div className="flex flex-col items-center justify-center gap-3 w-24 flex-shrink-0">
                <DottedLine />
                <button onClick={run} disabled={!ready}
                  className="group relative w-16 h-16 rounded-full transition-all duration-300 disabled:cursor-not-allowed"
                  style={{
                    background: ready ? "rgba(255,255,255,0.92)" : "rgba(255,255,255,0.04)",
                    border: ready ? "none" : "1px solid rgba(255,255,255,0.1)",
                    boxShadow: ready ? "0 0 0 1px rgba(255,255,255,0.1), 0 0 40px rgba(255,255,255,0.08)" : "none",
                    opacity: ready ? 1 : 0.4,
                  }}>
                  {/* Pulse ring */}
                  {ready && <span className="absolute inset-0 rounded-full animate-ping" style={{ background: "rgba(255,255,255,0.08)" }} />}
                  <div className="flex flex-col items-center justify-center h-full gap-0.5">
                    <Icon name="arrow-r" size={16} className={ready ? "text-black" : "text-white/40"} />
                    <span className="text-[8px] font-bold tracking-widest uppercase" style={{ color: ready ? "#000" : "rgba(255,255,255,0.3)", letterSpacing: "0.12em" }}>RUN</span>
                  </div>
                </button>
                <DottedLine />
              </div>

              {/* TARGET */}
              <Panel label="Target" sub="To · write" icon="table">
                <Field label="Catalog" value={tgtCat} onChange={v => { setTgtCat(v); setTgtSch(""); }} options={cats} placeholder="Select catalog" />
                <Field label="Schema" value={tgtSch} onChange={setTgtSch} disabled={!tgtCat} options={tgtSchs} placeholder="Select schema" />
                {tgtTbls.length > 0 ? (
                  <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.12em] mb-2 flex-shrink-0" style={{ color: "rgba(255,255,255,0.28)" }}>
                      {tgtTbls.length} Tables · {tgtTbls.reduce((n, t) => n + t.columns.length, 0)} Fields
                    </p>
                    <div className="flex-1 overflow-auto space-y-2 pr-1 -mr-1">
                      {tgtTbls.map(t => (
                        <TblBlock key={t.id} tbl={t} highlighted={[]} />
                      ))}
                    </div>
                  </div>
                ) : (
                  <EmptyHint icon="grid" text="Select a schema to preview target tables" />
                )}
              </Panel>
            </div>

            {/* HISTORY SECTION */}
            <div className="mt-4 flex-shrink-0">
              <h3 className="text-[12px] font-semibold tracking-wide uppercase mb-3" style={{ color: "rgba(255,255,255,0.4)" }}>Recent Mapping Jobs</h3>
              {history.length > 0 ? (
                <div className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.02)" }}>
                  <table className="w-full text-left text-[11px]">
                    <thead style={{ background: "rgba(255,255,255,0.03)", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                      <tr>
                        <th className="px-4 py-3 font-semibold text-white/50">Job ID</th>
                        <th className="px-4 py-3 font-semibold text-white/50">Name (Source → Target)</th>
                        <th className="px-4 py-3 font-semibold text-white/50">Status</th>
                        <th className="px-4 py-3 font-semibold text-white/50">Created At</th>
                        <th className="px-4 py-3 font-semibold text-white/50 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.04]">
                      {history.map(job => {
                        let stageName = "Schema Mapping Stage";
                        let stageColor = "#60a5fa";
                        let stageBg = "rgba(59,130,246,0.1)";
                        
                        if (job.status === "published") {
                           if (job.latest_run_id) {
                              stageName = "Migration Stage";
                              stageColor = "#34d399";
                              stageBg = "rgba(52,211,153,0.1)";
                           } else {
                              stageName = "Transformation/Cleaning Stage";
                              stageColor = "#a78bfa";
                              stageBg = "rgba(167,139,250,0.1)";
                           }
                        }

                        return (
                          <tr key={job.id} className="hover:bg-white/[0.02] transition-colors">
                            <td className="px-4 py-3 font-mono text-white/60">#{job.id}</td>
                            <td className="px-4 py-3 font-mono text-white/80">{job.name}</td>
                            <td className="px-4 py-3">
                              <span className="px-2 py-1 rounded-sm text-[9px] uppercase tracking-wider font-bold"
                                style={{ background: stageBg, color: stageColor }}>
                                {stageName}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-white/40">{new Date(job.created_at).toLocaleString()}</td>
                            <td className="px-4 py-3 text-right">
                              {job.status === "draft" ? (
                                <button onClick={() => resumeJob(job)} className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 text-white font-medium transition-colors">
                                  Resume
                                </button>
                              ) : job.latest_run_id ? (
                                <button onClick={() => router.push(`/dashboard/migration-status?mappingId=${job.id}&runId=${job.latest_run_id}`)} className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 text-white font-medium transition-colors">
                                  Open
                                </button>
                              ) : (
                                <button onClick={() => router.push(`/dashboard/transformation?mappingId=${job.id}`)} className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 text-white font-medium transition-colors">
                                  Open
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="py-8 text-center rounded-xl" style={{ border: "1px dashed rgba(255,255,255,0.1)" }}>
                  <p className="text-[11px] text-white/40">No mapping jobs found.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ═══ RESULTS MODE ════════════════════════════════════════════════ */}
        {mode === "run" && (
          <div className="h-full flex gap-3">

            {/* Source sidebar */}
            <aside className="w-52 flex-shrink-0 rounded-xl flex flex-col overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
              <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                <p className="text-[9px] font-bold uppercase tracking-[0.12em]" style={{ color: "rgba(255,255,255,0.28)" }}>Source</p>
                <p className="text-xs font-semibold mt-0.5 truncate" style={{ color: "rgba(255,255,255,0.8)" }}>{srcTbl}</p>
                <p className="text-[10px] truncate" style={{ color: "rgba(255,255,255,0.25)" }}>{srcCat}.{srcSch}</p>
              </div>
              <div className="flex-1 overflow-auto p-2 space-y-px">
                {srcObj?.columns.map(c => (
                  <div key={c.id} className="flex items-center justify-between px-2.5 py-1.5 rounded-md hover:bg-white/[0.03] transition-colors">
                    <div className="flex items-center gap-1.5 min-w-0">
                      {c.is_primary_key && <Icon name="key" size={9} className="flex-shrink-0" style={{ color: "rgba(255,255,255,0.35)" } as any} />}
                      <span className="text-[11px] font-mono truncate" style={{ color: "rgba(255,255,255,0.7)" }}>{c.column_name}</span>
                    </div>
                    <TypePill type={c.data_type} />
                  </div>
                ))}
              </div>
            </aside>

            {/* Main panel */}
            <div className="flex-1 flex flex-col overflow-hidden rounded-xl" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.015)" }}>
              {/* Top bar */}
              <div className="px-5 py-3 flex items-center justify-between flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                <div className="flex items-center gap-3">
                  <div className="flex bg-white/5 rounded-lg p-0.5">
                    <button onClick={() => setActiveTab('list')} className={`px-3 py-1 text-[11px] font-semibold rounded-md transition-colors ${activeTab === 'list' ? 'bg-white/10 text-white' : 'text-white/40 hover:text-white/70'}`}>List View</button>
                    <button onClick={() => setActiveTab('erd')} className={`px-3 py-1 text-[11px] font-semibold rounded-md transition-colors ${activeTab === 'erd' ? 'bg-white/10 text-white' : 'text-white/40 hover:text-white/70'}`}>ERD Diagram</button>
                  </div>
                  {!loading && suggestions.length > 0 && suggestions[0].status !== "error" && suggestions[0].target_table !== "ERROR" && (
                    <span className="text-[10px] px-2 py-0.5 rounded-sm font-semibold ml-2" style={{ background: "rgba(255,255,255,0.07)", color: "rgba(255,255,255,0.5)" }}>
                      {suggestions.length} match{suggestions.length !== 1 ? "es" : ""}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 font-mono" style={{ fontSize: 10, color: "rgba(255,255,255,0.22)" }}>
                  <span className="truncate max-w-[140px]">{srcCat}.{srcSch}.{srcTbl}</span>
                  <Icon name="arrow-r" size={11} />
                  <span className="truncate max-w-[120px]">{tgtCat}.{tgtSch}</span>
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-auto p-5">

                {activeTab === 'erd' && !loading && (
                  <div className="w-full h-full min-h-[500px] relative rounded-lg overflow-hidden" style={{ border: '1px solid rgba(255,255,255,0.05)' }}>
                    <ReactFlow 
                      nodes={erdNodes} 
                      edges={erdEdges} 
                      nodeTypes={nodeTypes}
                      fitView
                      className="bg-[#0a0a0f]"
                    >
                      <Background variant={BackgroundVariant.Dots} color="rgba(255,255,255,0.15)" gap={20} size={2} />
                      <Controls className="!bg-black/50 !border-white/10 !fill-white" />
                    </ReactFlow>
                  </div>
                )}

                {activeTab === 'list' && (
                  <>
                {loading ? (
                  <div className="h-full flex flex-col items-center justify-center gap-6">
                    {/* Animated scanner box */}
                    <div className="relative w-48 h-32 rounded-lg overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.015)" }}>
                      <ScanLine />
                      {/* Grid dots */}
                      {Array.from({ length: 12 }).map((_, i) => (
                        <div key={i} className="absolute w-px h-px rounded-full" style={{ background: "rgba(255,255,255,0.2)", left: `${(i % 4) * 33 + 8}%`, top: `${Math.floor(i / 4) * 40 + 15}%`, animationDelay: `${i * 0.1}s` }} />
                      ))}
                      <p className="absolute bottom-3 left-0 right-0 text-center text-[9px] font-mono uppercase tracking-widest" style={{ color: "rgba(255,255,255,0.2)" }}>Embedding…</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium" style={{ color: "rgba(255,255,255,0.6)" }}>Computing semantic similarity</p>
                      <p className="text-xs mt-1.5" style={{ color: "rgba(255,255,255,0.25)" }}>Vector embeddings generated · cosine distance calculated<br />Typically completes in 60–90 seconds</p>
                    </div>
                    {/* Pulsing dots */}
                    <div className="flex gap-1.5">
                      {[0,1,2].map(i => <div key={i} className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "rgba(255,255,255,0.3)", animationDelay: `${i * 0.3}s` }} />)}
                    </div>
                  </div>

                ) : suggestions.length > 0 && (suggestions[0].status === "error" || suggestions[0].target_table === "ERROR") ? (
                  <div className="h-full flex flex-col items-center justify-center gap-4 text-center max-w-md mx-auto">
                    <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ border: "1px solid rgba(255,255,255,0.12)", background: "rgba(255,255,255,0.04)" }}>
                      <Icon name="x-circle" size={18} className="opacity-60" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold" style={{ color: "rgba(255,255,255,0.7)" }}>Databricks API Error</p>
                      <pre className="text-[10px] font-mono mt-3 p-4 rounded-lg text-left break-words whitespace-pre-wrap" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.45)", maxWidth: "100%" }}>
                        {suggestions[0].reason}
                      </pre>
                    </div>
                    <button onClick={reset} className="text-xs px-4 py-2 rounded-md transition-colors hover:bg-white/[0.06]" style={{ border: "1px solid rgba(255,255,255,0.1)", color: "rgba(255,255,255,0.45)" }}>
                      Try Again
                    </button>
                  </div>

                ) : suggestions.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center gap-3" style={{ color: "rgba(255,255,255,0.2)" }}>
                    <Icon name="info" size={28} />
                    <p className="text-sm">No matches above confidence threshold</p>
                    <p className="text-xs" style={{ color: "rgba(255,255,255,0.15)" }}>Try selecting a different source table or target schema</p>
                  </div>

                ) : (
                  <div className="max-w-xl mx-auto space-y-2">
                    <p className="text-[9px] uppercase tracking-[0.16em] text-center mb-5" style={{ color: "rgba(255,255,255,0.18)" }}>Ordered by confidence · hover row for reasoning</p>
                    {suggestions.map((s, i) => (
                      <MatchRow key={i} s={s} rank={i + 1} />
                    ))}
                  </div>
                )}
                  </>
                )}
              </div>
            </div>

            {/* Target sidebar */}
            <aside className="w-52 flex-shrink-0 rounded-xl flex flex-col overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
              <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                <p className="text-[9px] font-bold uppercase tracking-[0.12em]" style={{ color: "rgba(255,255,255,0.28)" }}>Target Schema</p>
                <p className="text-xs font-semibold mt-0.5 truncate" style={{ color: "rgba(255,255,255,0.8)" }}>{tgtSch}</p>
                <p className="text-[10px] truncate" style={{ color: "rgba(255,255,255,0.25)" }}>{tgtCat}</p>
              </div>
              <div className="flex-1 overflow-auto p-2 space-y-2">
                {tgtTbls.map(t => (
                  <TblBlock key={t.id} tbl={t} highlighted={suggestions.filter(s => s.target_table === t.table_name).map((s: any) => s.target_column)} />
                ))}
              </div>
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Panel({ label, sub, icon, children }: { label: string; sub: string; icon: string; children: React.ReactNode }) {
  return (
    <div className="flex-1 rounded-xl flex flex-col overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.02)" }}>
      <div className="px-5 py-4 flex items-center gap-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.01)" }}>
        <div className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0" style={{ border: "1px solid rgba(255,255,255,0.12)", background: "rgba(255,255,255,0.04)" }}>
          <Icon name={icon} size={13} />
        </div>
        <div>
          <p className="text-[13px] font-semibold leading-none" style={{ color: "rgba(255,255,255,0.85)" }}>{label}</p>
          <p className="text-[10px] mt-0.5" style={{ color: "rgba(255,255,255,0.3)" }}>{sub}</p>
        </div>
      </div>
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-4 gap-3">
        {children}
      </div>
    </div>
  );
}

function ColList({ cols, count, label }: { cols: Col[]; count: number; label: string }) {
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] mb-2 flex-shrink-0" style={{ color: "rgba(255,255,255,0.28)" }}>{count} {label}</p>
      <div className="flex-1 overflow-auto space-y-px rounded-lg overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.06)", background: "rgba(0,0,0,0.2)" }}>
        {cols.map(c => (
          <div key={c.id} className="flex items-center justify-between px-3 py-2 hover:bg-white/[0.03] transition-colors" style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
            <div className="flex items-center gap-2 min-w-0">
              {c.is_primary_key && <Icon name="key" size={9} className="flex-shrink-0 opacity-50" />}
              <span className="text-[11px] font-mono truncate" style={{ color: "rgba(255,255,255,0.72)" }}>{c.column_name}</span>
            </div>
            <TypePill type={c.data_type} />
          </div>
        ))}
      </div>
    </div>
  );
}

function TblBlock({ tbl, highlighted }: { tbl: Tbl; highlighted: string[] }) {
  const name = tbl.table_name.split(".").pop() ?? tbl.table_name;
  const isActive = highlighted.length > 0;
  return (
    <div className="rounded-lg overflow-hidden" style={{ border: `1px solid ${isActive ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.06)"}`, background: isActive ? "rgba(255,255,255,0.03)" : "transparent", transition: "border-color 0.3s, background 0.3s" }}>
      <div className="px-3 py-1.5 flex items-center gap-2" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.02)" }}>
        <Icon name="table" size={10} className="opacity-40 flex-shrink-0" />
        <span className="text-[10px] font-semibold uppercase tracking-wide truncate" style={{ color: "rgba(255,255,255,0.55)" }}>{name}</span>
        {isActive && (
          <span className="ml-auto flex items-center gap-1 text-[9px] flex-shrink-0" style={{ color: "rgba(255,255,255,0.4)" }}>
            <Icon name="check" size={9} />
            {highlighted.length}
          </span>
        )}
      </div>
      {tbl.columns.map(c => {
        const hit = highlighted.includes(c.column_name);
        return (
          <div key={c.id} className="flex items-center justify-between px-3 py-1.5 transition-colors" style={{ borderBottom: "1px solid rgba(255,255,255,0.04)", background: hit ? "rgba(255,255,255,0.05)" : "transparent" }}>
            <div className="flex items-center gap-1.5 min-w-0">
              {hit && <div className="w-1 h-1 rounded-full flex-shrink-0" style={{ background: "rgba(255,255,255,0.6)" }} />}
              {c.is_primary_key && !hit && <Icon name="key" size={8} className="opacity-30 flex-shrink-0" />}
              <span className="text-[10px] font-mono truncate" style={{ color: hit ? "rgba(255,255,255,0.8)" : "rgba(255,255,255,0.4)" }}>{c.column_name}</span>
            </div>
            <TypePill type={c.data_type} />
          </div>
        );
      })}
    </div>
  );
}

function MatchRow({ s, rank }: { s: any; rank: number }) {
  const [hovered, setHovered] = useState(false);
  const pct = typeof s.confidence === "number" ? s.confidence : 0;
  return (
    <div onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      className="rounded-xl p-4 transition-all duration-200 cursor-default relative"
      style={{ border: `1px solid ${hovered ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.07)"}`, background: hovered ? "rgba(255,255,255,0.04)" : "rgba(255,255,255,0.02)" }}>
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-bold tabular-nums w-4 text-center flex-shrink-0" style={{ color: "rgba(255,255,255,0.2)" }}>{rank}</span>
        {/* source */}
        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase tracking-widest mb-1" style={{ color: "rgba(255,255,255,0.25)" }}>Source</p>
          <p className="text-[13px] font-mono font-medium truncate" style={{ color: "rgba(255,255,255,0.85)" }}>{s.source_column}</p>
          <TypePill type={s.source_type} />
        </div>
        {/* confidence */}
        <div className="flex-shrink-0 w-10 text-center z-10">
          <p className="text-[18px] font-bold leading-none tabular-nums" style={{ color: pct >= 80 ? "rgba(255,255,255,0.85)" : pct >= 60 ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.3)" }}>
            {pct.toFixed(0)}
          </p>
          <p className="text-[8px] uppercase tracking-widest mt-0.5" style={{ color: "rgba(255,255,255,0.2)" }}>pct</p>
        </div>
        
        {/* Animated Arrow */}
        <div className="flex-shrink-0 flex items-center justify-center mx-1 w-12 relative">
          <svg width="100%" height="20" viewBox="0 0 48 20" fill="none" xmlns="http://www.w3.org/2000/svg">
            {/* Background track */}
            <path d="M0 10H42" stroke="rgba(255,255,255,0.06)" strokeWidth="1.5" strokeDasharray="3 4" />
            {/* Animated dashed line */}
            <path d="M0 10H42" stroke={pct >= 80 ? "rgba(255,255,255,0.6)" : pct >= 60 ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.15)"} strokeWidth="1.5" strokeDasharray="3 4">
              <animate attributeName="stroke-dashoffset" from="7" to="0" dur="0.6s" repeatCount="indefinite" />
            </path>
            {/* Arrow head */}
            <path d="M38 6L42 10L38 14" stroke={pct >= 80 ? "rgba(255,255,255,0.6)" : pct >= 60 ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.15)"} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        {/* target */}
        <div className="flex-1 min-w-0 text-right">
          <p className="text-[9px] uppercase tracking-widest mb-1" style={{ color: "rgba(255,255,255,0.25)" }}>{s.target_table?.split(".").pop()}</p>
          <p className="text-[13px] font-mono font-medium truncate" style={{ color: "rgba(255,255,255,0.85)" }}>{s.target_column}</p>
          <div className="flex justify-end"><TypePill type={s.target_type} /></div>
        </div>
      </div>
      <div className="mt-3"><Bar pct={pct} /></div>

      {/* Reasoning on hover */}
      {hovered && s.reason && (
        <div className="absolute bottom-full left-0 right-0 mb-1.5 p-3 rounded-lg z-20 animate-in fade-in duration-150"
          style={{ background: "#1a1a1a", border: "1px solid rgba(255,255,255,0.1)", boxShadow: "0 8px 32px rgba(0,0,0,0.5)" }}>
          <p className="text-[9px] uppercase tracking-[0.12em] mb-1.5" style={{ color: "rgba(255,255,255,0.3)" }}>AI Reasoning</p>
          <p className="text-[11px] leading-relaxed" style={{ color: "rgba(255,255,255,0.6)" }}>{s.reason}</p>
        </div>
      )}
    </div>
  );
}

function EmptyHint({ icon, text }: { icon: string; text: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-2.5 rounded-lg" style={{ border: "1px dashed rgba(255,255,255,0.07)" }}>
      <Icon name={icon} size={20} className="opacity-15" />
      <p className="text-[11px] text-center max-w-[160px]" style={{ color: "rgba(255,255,255,0.22)" }}>{text}</p>
    </div>
  );
}

function DottedLine() {
  return (
    <div className="flex flex-col items-center gap-0.5 flex-1">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="w-px h-1 rounded-full" style={{ background: "rgba(255,255,255,0.1)" }} />
      ))}
    </div>
  );
}
