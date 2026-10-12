"use client";
import { useState, type RefObject } from "react";
import { downloadChartAsPng, downloadCsv } from "../lib/format";
import type { VizQueryResponse } from "../lib/types";

interface ExportMenuProps {
  result: VizQueryResponse | null;
  containerRef: RefObject<HTMLDivElement | null>;
  chartType: string;
}

export default function ExportMenu({ result, containerRef, chartType }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disabled = !result || result.rows.length === 0;

  const exportCsv = () => {
    if (!result) return;
    downloadCsv(result.columns, result.rows, `visualize-export-${Date.now()}.csv`);
    setOpen(false);
  };

  const exportPng = () => {
    setError(null);
    if (chartType === "table") {
      setError("PNG export isn't available for the table view — use CSV instead.");
      return;
    }
    if (!containerRef.current) return;
    try {
      downloadChartAsPng(containerRef.current, `visualize-export-${Date.now()}.png`);
      setOpen(false);
    } catch {
      setError("Failed to export PNG.");
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="px-3 py-2 text-xs font-semibold text-fg-muted border border-border-strong rounded-lg hover:bg-surface-overlay disabled:opacity-50"
      >
        Export ▾
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-48 rounded-xl bg-surface border border-border shadow-2xl z-40 p-1.5">
          <button type="button" onClick={exportCsv} className="w-full text-left px-3 py-2 text-xs rounded-lg hover:bg-surface-overlay text-fg-muted">
            Export data as CSV
          </button>
          <button type="button" onClick={exportPng} className="w-full text-left px-3 py-2 text-xs rounded-lg hover:bg-surface-overlay text-fg-muted">
            Export chart as PNG
          </button>
          {error && <p className="px-3 py-1 text-[11px] text-danger">{error}</p>}
        </div>
      )}
    </div>
  );
}
