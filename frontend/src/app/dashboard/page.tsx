"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWidgetData } from "./hooks/useWidgetData";
import type { DashboardSummary, DQSummary, TimeRange } from "./types";
import { DataQualitySection } from "./components/DataQualitySection";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
interface Connector { id: number; name: string; type: string; }
interface DriftAlert { id: number; connection_name: string | null; created_at: string; payload: Record<string, unknown> | null; }

const RANGE_KEY = "dashboard_time_range";
const POLL_MS = Number(process.env.NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS ?? 30_000);

import React from 'react';
function getIcon(name: string) {
  const icons: Record<string, React.ReactNode> = {
    'chart-bar': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>,
    'database': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>,
    'gem': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 3h12l4 6-10 13L2 9Z"/></svg>,
    'target': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>,
    'alert': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01"/></svg>,
    'lock': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>,
    'shield': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>,
    'robot': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/></svg>,
    'document': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>,
    'plug': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/></svg>,
    'link': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>,
    'play': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>,
    'rocket': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>,
    'chart-line': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>,
    'clock': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>,
  };
  if (!name || /[\u1000-\uFFFF]/.test(name)) return icons['chart-bar'];
  return icons[name] || icons['chart-bar'];
}

function VerticalKpi({ kpi }: { kpi: any }) {
  const isPercent = String(kpi.label).includes('%') || String(kpi.label).includes('Score');
  return (
    <div className="flex flex-col justify-center p-6 bg-transparent border-b border-white/5 last:border-b-0 hover:bg-white/[0.02] transition-colors cursor-pointer">
      <h3 className="text-[13px] text-white/50 font-medium mb-3 tracking-wide">{kpi.label}</h3>
      <div className="text-4xl font-bold text-[#3b82f6] tracking-tight">{kpi.value}{isPercent ? '%' : ''}</div>
    </div>
  )
}

export default function DashboardPage() {
  const [range, setRange] = useState<TimeRange>(() => {
    if (typeof window !== "undefined") {
      const s = localStorage.getItem(RANGE_KEY);
      if (s === "24h" || s === "7d" || s === "30d") return s;
    }
    return "7d";
  });

  const summary = useWidgetData<DashboardSummary>(
    (signal) => api.get<DashboardSummary>(`/api/v1/dashboard/summary?range=${range}`, { signal }),
    [range],
  );
  const drift = useWidgetData<DriftAlert[]>(
    (signal) => api.get<DriftAlert[]>("/api/v1/audit/?event_type=schema_drift_detected&page_size=5", { signal }),
    [],
  );
  const connectors = useWidgetData<Connector[]>(
    (signal) => api.get<Connector[]>("/api/v1/connectors/", { signal }),
    [],
  );

  const dataQuality = useWidgetData<DQSummary>(
    (signal) => api.get<DQSummary>("/api/v1/data-quality/summary?table_limit=100", { signal }),
    [],
  );

  const { refetch, isLoading } = summary;
  const refetchDataQuality = dataQuality.refetch;
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.hidden) return;
      if (!isLoading) refetch();
      refetchDataQuality();
    }, POLL_MS);
    return () => clearInterval(interval);
  }, [refetch, isLoading, refetchDataQuality]);

  const kpis = summary.data?.kpis ?? [];

  const mockTimeseriesData = [
    { name: 'Mon', migrations: 2, visualizations: 4, hours_saved: 4 },
    { name: 'Tue', migrations: 3, visualizations: 6, hours_saved: 8 },
    { name: 'Wed', migrations: 2, visualizations: 8, hours_saved: 12 },
    { name: 'Thu', migrations: 5, visualizations: 10, hours_saved: 16 },
    { name: 'Fri', migrations: 8, visualizations: 12, hours_saved: 24 },
    { name: 'Sat', migrations: 12, visualizations: 25, hours_saved: 36 },
    { name: 'Sun', migrations: 14, visualizations: 42, hours_saved: 52 },
  ];

  return (
    <div className="flex flex-col h-full bg-[#09090b] text-white">
      <div className="flex-1 overflow-y-auto p-6 lg:p-10 max-w-7xl mx-auto w-full">
        
        {/* Page header */}
        <div className="flex items-start justify-between mb-8">
          <h1 className="text-3xl font-bold text-white tracking-tight">Analytics</h1>
        </div>

        {/* Filter Bar */}
        <div className="flex flex-wrap lg:flex-nowrap items-center gap-4 mb-8 bg-[#111] border border-white/5 p-2 rounded-xl">
          <div className="flex items-center flex-1 max-w-sm px-4 py-2">
            <select className="bg-transparent border-none outline-none text-sm text-white/80 w-full appearance-none cursor-pointer">
              <option value="all">All analytics tags</option>
              <option value="prod">Production</option>
              <option value="dev">Development</option>
            </select>
          </div>
          
          <div className="flex items-center gap-4 flex-1 px-4 py-2 border-l border-white/5">
            <svg className="w-4 h-4 text-white/40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg>
            <div className="flex items-center justify-between flex-1 text-sm text-white/80">
              <span>06/15/2022</span>
              <span className="text-white/40 mx-4">→</span>
              <span>06/21/2022</span>
            </div>
          </div>

          <button className="bg-[#0ea5e9] hover:bg-[#0284c7] text-white px-6 py-2 rounded-lg text-sm font-semibold transition-all shadow-[0_0_15px_rgba(14,165,233,0.3)] ml-auto">
            Apply filters
          </button>
        </div>

        {/* Main Section: KPIs + Chart */}
        <div className="flex flex-col lg:flex-row gap-6 mb-12">
          {/* Left: Stacked KPIs */}
          <div className="w-full lg:w-1/4 flex flex-col bg-[#111] border border-white/5 rounded-xl overflow-hidden shadow-sm">
            {kpis.slice(0, 3).map((kpi, idx) => (
              <VerticalKpi key={idx} kpi={kpi} />
            ))}
          </div>

          {/* Right: Main Chart */}
          <div className="w-full lg:w-3/4 bg-[#111] border border-white/5 rounded-xl p-8 shadow-sm flex flex-col">
            <div className="flex-1 h-[350px] w-full mt-4">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={mockTimeseriesData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="name" stroke="rgba(255,255,255,0.3)" fontSize={12} tickLine={false} axisLine={false} dy={10} />
                  <YAxis stroke="rgba(255,255,255,0.3)" fontSize={12} tickLine={false} axisLine={false} dx={-10} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#111', borderColor: 'rgba(255,255,255,0.1)', borderRadius: '8px', color: '#fff' }}
                    itemStyle={{ color: '#fff' }}
                  />
                  <Line type="monotone" dataKey="migrations" stroke="#6ee7b7" strokeWidth={2} dot={{ r: 4, strokeWidth: 2, fill: "#09090b" }} activeDot={{ r: 6 }} />
                  <Line type="monotone" dataKey="visualizations" stroke="#c084fc" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 4, strokeWidth: 2, fill: "#09090b" }} />
                  <Line type="monotone" dataKey="hours_saved" stroke="#38bdf8" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 4, strokeWidth: 2, fill: "#09090b" }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Data quality (reported by the Databricks PySpark job) */}
        <DataQualitySection widget={dataQuality} />

        {/* Bottom Section: Tables */}
        <div className="pt-8">
          <div className="flex items-center gap-2 mb-2">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <h2 className="text-xl font-bold text-white tracking-tight">Platform analytics</h2>
          </div>
          <p className="text-white/50 text-sm mb-8">Gain insight into the most frequent active connectors, and recent drift alerts.</p>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-10">
            {/* Table 1: Top Connectors */}
            <div>
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-semibold text-white">Top Active Connectors</h3>
                <button className="text-[#3b82f6] text-sm hover:underline flex items-center gap-1">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                  View all
                </button>
              </div>
              <div className="border border-white/10 rounded-lg overflow-hidden bg-[#111]">
                <table className="w-full text-left text-sm">
                  <thead className="bg-white/5 text-white/50 text-xs">
                    <tr>
                      <th className="px-4 py-3 font-medium">Connector Name</th>
                      <th className="px-4 py-3 font-medium">Type</th>
                      <th className="px-4 py-3 font-medium text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-white/80">
                    {connectors.data?.slice(0, 4).map((c, i) => (
                      <tr key={i} className="hover:bg-white/5">
                        <td className="px-4 py-3 text-[#3b82f6] font-medium">{c.name}</td>
                        <td className="px-4 py-3"><span className="px-2 py-1 bg-white/10 rounded text-xs">{c.type}</span></td>
                        <td className="px-4 py-3 flex gap-2 text-white/40 justify-end">
                           <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                        </td>
                      </tr>
                    ))}
                    {!connectors.data?.length && (
                      <tr><td colSpan={3} className="px-4 py-8 text-center text-white/40">No connectors found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Table 2: Recent Drift */}
            <div>
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-semibold text-white">Recent Drift Alerts</h3>
                <button className="text-[#3b82f6] text-sm hover:underline flex items-center gap-1">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                  View all
                </button>
              </div>
              <div className="border border-white/10 rounded-lg overflow-hidden bg-[#111]">
                <table className="w-full text-left text-sm">
                  <thead className="bg-white/5 text-white/50 text-xs">
                    <tr>
                      <th className="px-4 py-3 font-medium">Connection</th>
                      <th className="px-4 py-3 font-medium">Detected At</th>
                      <th className="px-4 py-3 font-medium text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-white/80">
                    {drift.data?.slice(0, 4).map((d, i) => (
                      <tr key={i} className="hover:bg-white/5">
                        <td className="px-4 py-3 text-[#3b82f6] font-medium">{d.connection_name ?? "Unknown"}</td>
                        <td className="px-4 py-3"><span className="px-2 py-1 bg-white/10 rounded text-xs">{new Date(d.created_at).toLocaleDateString()}</span></td>
                        <td className="px-4 py-3 flex gap-2 text-white/40 justify-end">
                           <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                        </td>
                      </tr>
                    ))}
                    {!drift.data?.length && (
                      <tr><td colSpan={3} className="px-4 py-8 text-center text-white/40">No alerts found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
