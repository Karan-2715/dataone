"use client";
import Link from "next/link";
import type { KPITileData } from "../types";

// Value accent per module, matching the pre-existing dashboard palette.
const MODULE_COLORS: Record<string, string> = {
  connectors: "text-blue-400",
  mappings: "text-white",
  pipelines: "text-emerald-400",
  query: "text-cyan-400",
  security: "text-rose-400",
  schema_intel: "text-amber-400",
  autopilot: "text-white/80",
};

// 1234 → "1,234"; 12345 → "12.3k"; 1234567 → "1.2M" (dashboard_tasks #4).
export function formatKPIValue(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString();
}

interface KPITileProps {
  tile: KPITileData;
  isLoading?: boolean;
}

/**
 * KPITile — one KPI stat card (dashboard_tasks #4). Loaded tiles are a
 * drill-through <Link> to their owning module; error/unavailable tiles
 * are deliberately not clickable (no navigating into a broken module).
 */
export function KPITile({ tile, isLoading = false }: KPITileProps) {
  if (isLoading) {
    return (
      <div
        aria-hidden="true"
        className="p-5 rounded-2xl bg-surface-elevated border border-border backdrop-blur-sm flex flex-col gap-3"
      >
        <div className="h-4 w-2/3 rounded bg-surface-overlay animate-pulse" />
        <div className="h-8 w-1/3 rounded bg-surface-overlay animate-pulse" />
        <div className="h-3 w-1/2 rounded bg-surface-overlay animate-pulse" />
      </div>
    );
  }

  if (tile.status !== "loaded") {
    return (
      <div
        className="p-5 rounded-2xl bg-surface-elevated border border-red-500/20 backdrop-blur-sm flex flex-col gap-2"
        title={tile.error_message ?? undefined}
      >
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-fg-subtle truncate">{tile.label}</span>
          {tile.icon && <span className="text-xl">{tile.icon}</span>}
        </div>
        <div className="text-3xl font-bold text-fg-subtle">—</div>
        <div className="text-xs text-red-400/80 truncate">
          {tile.status === "unavailable"
            ? tile.error_message || "Not available"
            : tile.error_message || "Failed to load"}
        </div>
      </div>
    );
  }

  const showTrend = tile.trend && tile.trend !== "neutral" && tile.value > 0;
  const color = MODULE_COLORS[tile.module] ?? "text-fg-muted";

  return (
    <Link
      href={tile.link_url}
      className="p-5 rounded-2xl bg-surface-elevated border border-border backdrop-blur-sm flex flex-col gap-2 hover:border-border-strong transition-colors group"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-fg-subtle truncate">{tile.label}</span>
        {tile.icon && (
          <span className="text-xl group-hover:scale-110 transition-transform" role="img" aria-hidden="true">
            {tile.icon}
          </span>
        )}
      </div>
      <div className={`text-3xl font-bold ${color}`}>{formatKPIValue(tile.value)}</div>
      <div className="flex items-center gap-2 text-xs text-fg0 min-h-4">
        {showTrend && (
          <span className={tile.trend === "up" ? "text-red-400" : "text-emerald-400"}>
            {tile.trend === "up" ? "↑" : "↓"}
          </span>
        )}
        <span className="truncate">{tile.trend_label || tile.subtitle || ""}</span>
      </div>
    </Link>
  );
}
