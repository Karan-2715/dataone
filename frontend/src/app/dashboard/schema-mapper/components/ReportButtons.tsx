"use client";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

interface ReportResponse {
  content: string;
  filename: string;
}

interface ReportButtonsProps {
  mappingId: number;
}

function downloadMarkdown(report: ReportResponse) {
  const blob = new Blob([report.content], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = report.filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Enterprise v2, E15 — "Generate Documentation" / "Generate Migration Report"
 * toolbar actions. Both compose existing data (mapping_service,
 * mapping_validation_service, schema_comparison_service, risk_service) into
 * a downloadable Markdown artifact; nothing is invented client-side. */
export default function ReportButtons({ mappingId }: ReportButtonsProps) {
  const [busy, setBusy] = useState<"documentation" | "migration-report" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = async (kind: "documentation" | "migration-report") => {
    setBusy(kind);
    setError(null);
    try {
      const report = await api.get<ReportResponse>(`/api/v1/mappings/${mappingId}/${kind}`);
      downloadMarkdown(report);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Report generation failed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => void generate("documentation")}
        disabled={busy !== null}
        className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-surface-overlay text-fg-muted hover:bg-surface-overlay disabled:opacity-50"
        aria-label="Generate documentation"
      >
        {busy === "documentation" ? "Generating…" : "📄 Documentation"}
      </button>
      <button
        type="button"
        onClick={() => void generate("migration-report")}
        disabled={busy !== null}
        className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-surface-overlay text-fg-muted hover:bg-surface-overlay disabled:opacity-50"
        aria-label="Generate migration report"
      >
        {busy === "migration-report" ? "Generating…" : "📋 Migration Report"}
      </button>
      {error && <span className="text-[10px] text-danger">{error}</span>}
    </div>
  );
}
