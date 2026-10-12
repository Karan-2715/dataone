"use client";
import { useMemo, type RefObject } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Area, AreaChart,
  Pie, PieChart, Scatter, ScatterChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { ChartType, MeasureSpec, VizQueryResponse } from "../lib/types";

const SERIES_COLORS = [
  "var(--edge-ai)",
  "var(--edge-business-rule)",
  "var(--warning)",
  "var(--success)",
  "var(--danger)",
  "var(--accent)",
];
const tooltipStyle = { background: "var(--surface)", border: "1px solid var(--border-strong)", color: "var(--fg)" };

interface ChartCanvasProps {
  chartType: ChartType;
  result: VizQueryResponse | null;
  dimensions: string[];
  measures: MeasureSpec[];
  loading: boolean;
  error: string | null;
  containerRef: RefObject<HTMLDivElement | null>;
}

function toObjectRows(result: VizQueryResponse): Record<string, unknown>[] {
  return result.rows.map((row) => {
    const obj: Record<string, unknown> = {};
    result.columns.forEach((col, i) => { obj[col] = row[i]; });
    return obj;
  });
}

export default function ChartCanvas({ chartType, result, dimensions, measures, loading, error, containerRef }: ChartCanvasProps) {
  const data = useMemo(() => (result ? toObjectRows(result) : []), [result]);
  const measureKeys = useMemo(
    () => measures.map((m) => m.label || `${m.aggregation}_${m.field}`),
    [measures],
  );
  const dimensionKey = dimensions[0];

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-2 text-fg-subtle">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <span className="text-xs">Running query…</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md text-center text-sm text-danger bg-danger/10 border border-danger/20 rounded-lg px-4 py-3">
          {error}
        </div>
      </div>
    );
  }

  if (!result || data.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-10">
        <div className="max-w-sm text-center text-sm text-fg-subtle">
          {!result
            ? "Select fields to build your chart."
            : "No data matches your filters."}
        </div>
      </div>
    );
  }

  if (chartType === "kpi") {
    const key = measureKeys[0];
    const value = data[0]?.[key];
    return (
      <div ref={containerRef} className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="text-5xl font-bold text-fg">
            {typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(value)}
          </div>
          <div className="text-xs text-fg-subtle mt-2 uppercase tracking-wider">{key}</div>
        </div>
      </div>
    );
  }

  if (chartType === "table") {
    return (
      <div ref={containerRef} className="flex-1 overflow-auto p-2">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[10px] uppercase text-fg-subtle border-b border-border">
              {result.columns.map((c) => <th key={c} className="text-left px-3 py-2">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i} className="border-b border-border/60">
                {row.map((v, j) => (
                  <td key={j} className="px-3 py-1.5 text-fg-muted">{v === null ? "—" : String(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {result.truncated && (
          <p className="text-[11px] text-warning mt-2 px-3">
            Results truncated — showing the first {result.rows.length} rows.
          </p>
        )}
      </div>
    );
  }

  if (chartType === "pie") {
    const key = measureKeys[0];
    return (
      <div ref={containerRef} className="flex-1 p-4">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey={key} nameKey={dimensionKey} outerRadius="70%" label>
              {data.map((_, i) => <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />)}
            </Pie>
            <Tooltip contentStyle={tooltipStyle} />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (chartType === "scatter") {
    const [xKey, yKey] = measureKeys;
    return (
      <div ref={containerRef} className="flex-1 p-4">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart>
            <CartesianGrid stroke="var(--border)" />
            <XAxis dataKey={xKey} name={xKey} stroke="var(--fg-subtle)" fontSize={11} />
            <YAxis dataKey={yKey} name={yKey} stroke="var(--fg-subtle)" fontSize={11} />
            <Tooltip contentStyle={tooltipStyle} />
            <Scatter data={data} fill={SERIES_COLORS[0]} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const ChartComponent = chartType === "line" ? LineChart : chartType === "area" ? AreaChart : BarChart;

  return (
    <div ref={containerRef} className="flex-1 p-4">
      <ResponsiveContainer width="100%" height="100%">
        <ChartComponent data={data}>
          <CartesianGrid stroke="var(--border)" />
          <XAxis dataKey={dimensionKey} stroke="var(--fg-subtle)" fontSize={11} />
          <YAxis stroke="var(--fg-subtle)" fontSize={11} />
          <Tooltip contentStyle={tooltipStyle} />
          <Legend />
          {measureKeys.map((key, i) =>
            chartType === "line" ? (
              <Line key={key} type="monotone" dataKey={key} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ) : chartType === "area" ? (
              <Area key={key} type="monotone" dataKey={key} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} fill={SERIES_COLORS[i % SERIES_COLORS.length]} fillOpacity={0.3} />
            ) : (
              <Bar key={key} dataKey={key} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ),
          )}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
}
