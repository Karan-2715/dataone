"use client";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useWidgetData } from "../hooks/useWidgetData";

interface DemoDataStatus {
  loaded: boolean;
}

interface DemoDataBannerProps {
  /** Called after a successful load/remove so the caller can refresh its
   * own KPI/connector widgets without a manual page reload. */
  onChange?: () => void;
}

export function DemoDataBanner({ onChange }: DemoDataBannerProps) {
  const status = useWidgetData<DemoDataStatus>(
    (signal) => api.get<DemoDataStatus>("/api/v1/demo-data/status", { signal }),
    [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLoad = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.post("/api/v1/demo-data/load", {});
      status.refetch();
      onChange?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load demo data.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.delete("/api/v1/demo-data");
      status.refetch();
      onChange?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove demo data.");
    } finally {
      setBusy(false);
    }
  };

  if (status.isLoading || status.isError || !status.data) return null;

  if (status.data.loaded) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 rounded-xl bg-surface-elevated border border-border text-xs">
        <span className="text-fg-muted">
          <span className="text-emerald-400 font-semibold">●</span> Demo data is loaded on this workspace.
        </span>
        <div className="flex items-center gap-3">
          {error && <span className="text-red-400">{error}</span>}
          <button
            type="button"
            onClick={handleRemove}
            disabled={busy}
            className="px-3 py-1.5 rounded-lg border border-border-strong text-fg-muted hover:bg-surface-overlay transition-colors disabled:opacity-60"
          >
            {busy ? "Removing…" : "Remove"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl bg-gradient-to-br from-white/5 to-white/5 border border-white/10">
      <div>
        <h3 className="text-sm font-semibold text-fg">Explore with sample data</h3>
        <p className="text-xs text-fg-muted mt-0.5">
          Nothing is loaded yet. Load a set of sample connectors and datasets to try out the platform.
        </p>
        {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
      </div>
      <button
        type="button"
        onClick={handleLoad}
        disabled={busy}
        className="shrink-0 px-4 py-2 text-xs font-semibold text-accent-fg bg-accent rounded-xl hover:opacity-90 transition-all shadow-sm disabled:opacity-60"
      >
        {busy ? "Loading…" : "Load Demo Data"}
      </button>
    </div>
  );
}
