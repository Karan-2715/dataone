"use client";
/**
 * AI-Powered Data Visualization
 * User picks catalog → database → table. Databricks LLM automatically
 * analyzes the columns and generates an insightful multi-chart dashboard.
 */
import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────────
interface Connection { id: number; name: string; type: string; }
interface CatalogTable { id: number; table_name: string; columns: { column_name: string; data_type: string }[]; }

interface ChartSpec {
  chart_type: "bar" | "line" | "pie" | "scatter" | "area" | "kpi";
  title: string;
  insight: string;
  x_column: string | null;
  y_column: string | null;
  agg: string | null;
  group_by: string | null;
  color_palette: string[];
  priority: number;
}

interface AIInsightResponse {
  table_name: string;
  table_summary: string;
  charts: ChartSpec[];
  columns: { name: string; type: string }[];
  sample_rows: Record<string, unknown>[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function aggregateRows(
  rows: Record<string, unknown>[],
  spec: ChartSpec,
): Record<string, unknown>[] {
  const { x_column, y_column, agg, group_by } = spec;
  if (!rows.length) return [];

  const groupKey = group_by || x_column;
  if (!groupKey || !y_column) return rows.slice(0, 30);

  const buckets: Record<string, number[]> = {};
  for (const row of rows) {
    const k = String(row[groupKey] ?? "—");
    const v = Number(row[y_column] ?? 0);
    if (!buckets[k]) buckets[k] = [];
    buckets[k].push(isNaN(v) ? 0 : v);
  }

  const reduce = (vals: number[]) => {
    switch (agg) {
      case "sum": return vals.reduce((a, b) => a + b, 0);
      case "avg": return vals.reduce((a, b) => a + b, 0) / vals.length;
      case "min": return Math.min(...vals);
      case "max": return Math.max(...vals);
      default: return vals.length; // count
    }
  };

  return Object.entries(buckets)
    .map(([k, vals]) => ({ [groupKey]: k, [y_column]: +reduce(vals).toFixed(2) }))
    .sort((a, b) => (b[y_column] as number) - (a[y_column] as number))
    .slice(0, 20);
}

function computeKPI(rows: Record<string, unknown>[], spec: ChartSpec): string {
  if (!rows.length || !spec.y_column) return "—";
  const vals = rows.map((r) => Number(r[spec.y_column!] ?? 0)).filter((v) => !isNaN(v));
  if (!vals.length) return "—";
  switch (spec.agg) {
    case "sum": return vals.reduce((a, b) => a + b, 0).toLocaleString();
    case "avg": return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2);
    case "min": return Math.min(...vals).toLocaleString();
    case "max": return Math.max(...vals).toLocaleString();
    default: return vals.length.toLocaleString();
  }
}

const TOOLTIP_STYLE = {
  backgroundColor: "#1a1a1f",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 8,
  color: "#fff",
  fontSize: 12,
};

// ── Chart Renderer ────────────────────────────────────────────────────────────
function ChartCard({ spec, rows }: { spec: ChartSpec; rows: Record<string, unknown>[] }) {
  const data = spec.chart_type === "kpi" ? [] : aggregateRows(rows, spec);
  const palette = spec.color_palette?.length ? spec.color_palette : ["#6366f1", "#8b5cf6", "#ec4899"];
  const xKey = spec.group_by || spec.x_column || "";
  const yKey = spec.y_column || "";

  const renderChart = () => {
    switch (spec.chart_type) {
      case "bar":
        return (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey={xKey} tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
              <Bar dataKey={yKey} radius={[4, 4, 0, 0]}>
                {data.map((_, i) => (
                  <Cell key={i} fill={palette[i % palette.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        );
      case "line":
        return (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey={xKey} tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Line type="monotone" dataKey={yKey} stroke={palette[0]} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        );
      case "area":
        return (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <defs>
                <linearGradient id={`grad-${spec.priority}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={palette[0]} stopOpacity={0.35} />
                  <stop offset="95%" stopColor={palette[0]} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey={xKey} tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Area type="monotone" dataKey={yKey} stroke={palette[0]} fill={`url(#grad-${spec.priority})`} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        );
      case "pie": {
        const pieData = data.map((d) => ({ name: String(d[xKey] ?? "—"), value: Number(d[yKey] ?? 0) }));
        return (
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} innerRadius={40} paddingAngle={2}>
                {pieData.map((_, i) => <Cell key={i} fill={palette[i % palette.length]} />)}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend iconType="circle" iconSize={8} formatter={(v) => <span style={{ color: "rgba(255,255,255,0.6)", fontSize: 11 }}>{v}</span>} />
            </PieChart>
          </ResponsiveContainer>
        );
      }
      case "scatter":
        return (
          <ResponsiveContainer width="100%" height={220}>
            <ScatterChart margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey={xKey} name={xKey} tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis dataKey={yKey} name={yKey} tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ strokeDasharray: "3 3" }} />
              <Scatter data={data} fill={palette[0]} opacity={0.8} />
            </ScatterChart>
          </ResponsiveContainer>
        );
      case "kpi": {
        const kpiVal = computeKPI(rows, spec);
        return (
          <div className="flex flex-col items-center justify-center h-[220px]">
            <div className="text-5xl font-black tracking-tight" style={{ color: palette[0] }}>
              {kpiVal}
            </div>
            <div className="text-xs text-white/40 mt-3 uppercase tracking-widest">
              {spec.agg?.toUpperCase() ?? "VALUE"} of {spec.y_column}
            </div>
          </div>
        );
      }
      default: return null;
    }
  };

  return (
    <div className="bg-[#111318] border border-white/[0.06] rounded-2xl p-5 flex flex-col gap-3 hover:border-white/[0.1] transition-colors">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"
            style={{ background: `${palette[0]}22`, color: palette[0] }}>
            {spec.chart_type}
          </span>
        </div>
        <h3 className="text-[14px] font-semibold text-white leading-tight">{spec.title}</h3>
        <p className="text-[12px] text-white/40 mt-0.5 leading-snug">{spec.insight}</p>
      </div>
      {renderChart()}
    </div>
  );
}

// ── Skeleton Card ─────────────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div className="bg-[#111318] border border-white/[0.06] rounded-2xl p-5 animate-pulse">
      <div className="h-3 w-20 bg-white/10 rounded-full mb-3" />
      <div className="h-4 w-48 bg-white/10 rounded-full mb-2" />
      <div className="h-3 w-64 bg-white/[0.06] rounded-full mb-6" />
      <div className="h-[220px] bg-white/[0.04] rounded-xl" />
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function VisualizePage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [tables, setTables] = useState<any[]>([]);
  const [connId, setConnId] = useState<number | null>(null);
  
  const [srcCat, setSrcCat] = useState("");
  const [srcSch, setSrcSch] = useState("");
  const [srcTbl, setSrcTbl] = useState("");

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AIInsightResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load connections on mount
  useEffect(() => {
    api.get<Connection[]>("/api/v1/connectors/")
      .then((res) => {
        const list = Array.isArray(res) ? res : (res as any).connectors ?? [];
        setConnections(list);
        const db = list.find((c: any) => c.type?.toLowerCase() === "databricks");
        if (db) setConnId(db.id);
      })
      .catch(() => {});
  }, []);

  // Load schema and parse tables
  useEffect(() => {
    if (!connId) return;
    setTables([]);
    setSrcCat("");
    setSrcSch("");
    setSrcTbl("");
    setResult(null);
    api.get<{ schema: Record<string, any[]> }>(`/api/v1/connectors/${connId}/schema`)
      .then((res) => {
        let uid = 1;
        setTables(Object.entries(res.schema ?? {}).map(([name, cols]) => ({
          id: uid++, table_name: name,
          columns: (cols || []).map(c => ({ id: uid++, column_name: c.name, data_type: c.type ?? "", is_primary_key: c.primary_key ?? false })),
        })));
      })
      .catch(() => {});
  }, [connId]);

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

  const analyze = useCallback(async () => {
    if (!connId || !srcCat || !srcSch || !srcTbl) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.post<AIInsightResponse>("/api/v1/viz/ai-insights", {
        connection_id: connId,
        table_name: `${srcCat}.${srcSch}.${srcTbl}`,
      });
      setResult(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [connId, srcCat, srcSch, srcTbl]);

  const gridClass =
    result && result.charts.length > 0
      ? "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5"
      : "";

  return (
    <div className="min-h-full bg-[#0c0c0e] text-white flex flex-col relative overflow-hidden">
      {/* Background glow */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <div className="absolute top-[-15%] left-[-5%] w-[45%] h-[45%] bg-violet-500/[0.04] blur-[140px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-5%] w-[35%] h-[35%] bg-indigo-500/[0.04] blur-[120px] rounded-full" />
      </div>

      {/* Header */}
      <div className="relative z-10 border-b border-white/[0.06] px-8 pt-8 pb-6">
        <div className="max-w-[1600px] mx-auto">
          <div className="flex items-start justify-between flex-wrap gap-4">
            <div>
              <h1 className="text-2xl font-bold text-white mb-1">Data Visualization</h1>
              <p className="text-[14px] text-white/40">
                Select a table — Databricks AI instantly generates an insight dashboard tailored for stakeholders.
              </p>
            </div>

            {/* Controls */}
            <div className="flex items-center gap-3 flex-wrap">
              {/* Catalog picker */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Catalog</label>
                <select
                  value={srcCat}
                  onChange={(e) => { setSrcCat(e.target.value); setSrcSch(""); setSrcTbl(""); }}
                  className="bg-[#111318] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] text-white/80 focus:outline-none focus:border-white/20 min-w-[140px]"
                >
                  <option value="">— select —</option>
                  {cats.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>

              {/* Schema picker */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Schema</label>
                <select
                  value={srcSch}
                  onChange={(e) => { setSrcSch(e.target.value); setSrcTbl(""); }}
                  disabled={!srcCat}
                  className="bg-[#111318] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] text-white/80 focus:outline-none focus:border-white/20 min-w-[140px] disabled:opacity-40"
                >
                  <option value="">— select —</option>
                  {srcSchs.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>

              {/* Table picker */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Table</label>
                <select
                  value={srcTbl}
                  onChange={(e) => setSrcTbl(e.target.value)}
                  disabled={!srcSch}
                  className="bg-[#111318] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] text-white/80 focus:outline-none focus:border-white/20 min-w-[140px] disabled:opacity-40"
                >
                  <option value="">— select —</option>
                  {srcTbls.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>

              {/* Analyze button */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-transparent uppercase tracking-widest">-</label>
                <button
                  onClick={analyze}
                  disabled={loading || !connId || !srcTbl}
                  className="flex items-center gap-2 px-5 py-2 rounded-lg font-semibold text-[13px] transition-all duration-200 disabled:opacity-40"
                  style={{
                    background: loading ? "rgba(99,102,241,0.3)" : "linear-gradient(135deg,#6366f1,#8b5cf6)",
                    color: "#fff",
                  }}
                >
                  {loading ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                      Analyzing…
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                        <path d="M9.663 17h4.673M12 3v1m6.364 1.636-.707.707M21 12h-1M4 12H3m3.343-5.657-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                      </svg>
                      Generate Insights
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="relative z-10 flex-1 overflow-auto px-8 py-6">
        <div className="max-w-[1600px] mx-auto">

          {/* Error */}
          {error && (
            <div className="mb-6 bg-red-500/10 border border-red-500/20 rounded-xl px-5 py-4 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Loading skeletons */}
          {loading && (
            <div>
              <div className="mb-6">
                <div className="h-4 w-72 bg-white/10 rounded-full animate-pulse mb-2" />
                <div className="h-3 w-96 bg-white/[0.06] rounded-full animate-pulse" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
                {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
              </div>
            </div>
          )}

          {/* Results */}
          {!loading && result && (
            <>
              {/* Table summary banner */}
              <div className="mb-6 bg-[#111318] border border-white/[0.06] rounded-2xl px-6 py-5">
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                  <span className="text-[12px] text-white/40 uppercase tracking-widest font-bold">AI Analysis</span>
                  <span className="text-[12px] text-white/25">·</span>
                  <span className="text-[12px] text-white/40 font-mono">{result.table_name}</span>
                </div>
                <p className="text-[14px] text-white/70 leading-relaxed">{result.table_summary}</p>
                <div className="flex items-center gap-4 mt-3 text-[12px] text-white/30">
                  <span>{result.columns.length} columns analyzed</span>
                  <span>·</span>
                  <span>{result.sample_rows.length} sample rows</span>
                  <span>·</span>
                  <span>{result.charts.length} charts generated</span>
                </div>
              </div>

              {/* Charts grid */}
              <div className={gridClass}>
                {result.charts.map((chart, i) => (
                  <ChartCard key={i} spec={chart} rows={result.sample_rows} />
                ))}
              </div>

              {/* Column metadata strip */}
              <div className="mt-8 bg-[#111318] border border-white/[0.06] rounded-2xl px-6 py-5">
                <p className="text-[11px] text-white/30 uppercase tracking-widest font-bold mb-4">Schema</p>
                <div className="flex flex-wrap gap-2">
                  {result.columns.map((col) => (
                    <div key={col.name} className="flex items-center gap-2 bg-white/[0.04] border border-white/[0.06] rounded-lg px-3 py-1.5">
                      <span className="text-[12px] text-white/70 font-mono">{col.name}</span>
                      <span className="text-[10px] text-white/30 uppercase">{col.type}</span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Empty state */}
          {!loading && !result && !error && (
            <div className="flex flex-col items-center justify-center py-32 text-center">
              <div className="w-16 h-16 rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center mb-6">
                <svg className="w-7 h-7 text-white/20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path d="M9.663 17h4.673M12 3v1m6.364 1.636-.707.707M21 12h-1M4 12H3m3.343-5.657-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-white/60 mb-2">Select a table and click Generate Insights</h2>
              <p className="text-[13px] text-white/30 max-w-md">
                The Databricks AI will analyze your table columns and automatically create an executive-ready dashboard — no manual chart configuration needed.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
