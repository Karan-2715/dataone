"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { AuditFilters } from "../lib/types";

function buildExportQuery(filters: AuditFilters, format: "csv" | "json"): string {
  const params = new URLSearchParams({ format });
  if (filters.actor) params.set("actor", filters.actor);
  if (filters.module) params.set("module", filters.module);
  if (filters.event_type) params.set("event_type", filters.event_type);
  if (filters.outcome) params.set("outcome", filters.outcome);
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (filters.search) params.set("search", filters.search);
  return params.toString();
}

export default function ExportButton({ filters }: { filters: AuditFilters }) {
  const [busy, setBusy] = useState<"csv" | "json" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doExport = async (format: "csv" | "json") => {
    setBusy(format);
    setError(null);
    try {
      const { blob, filename } = await api.download(`/api/v1/audit/export?${buildExportQuery(filters, format)}`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => doExport("csv")}
        disabled={busy !== null}
        className="px-3 py-2 text-xs bg-surface-overlay hover:bg-surface-overlay text-fg-muted rounded-lg disabled:opacity-50"
      >
        {busy === "csv" ? "Exporting…" : "Export CSV"}
      </button>
      <button
        onClick={() => doExport("json")}
        disabled={busy !== null}
        className="px-3 py-2 text-xs bg-surface-overlay hover:bg-surface-overlay text-fg-muted rounded-lg disabled:opacity-50"
      >
        {busy === "json" ? "Exporting…" : "Export JSON"}
      </button>
      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  );
}
