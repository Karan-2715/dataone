"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { classNames, formatPercent, formatTimestamp } from "../lib/format";
import type { FieldMapping, Role, TransformationPayload } from "../lib/types";

interface PreviewRow {
  source_values: Record<string, unknown>;
  output: unknown;
}

interface PreviewResponse {
  available: boolean;
  reason: string | null;
  rows: PreviewRow[];
}

interface EdgeInspectorProps {
  edge: FieldMapping | null;
  mappingId: number | null;
  role: Role | null;
  canEdit: boolean;
  onEdit: (transformation: TransformationPayload) => void;
  onDelete: () => void;
}

export default function EdgeInspector({
  edge,
  mappingId,
  role,
  canEdit,
  onEdit,
  onDelete,
}: EdgeInspectorProps) {
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Reset stale preview state when a different edge is selected — a
  // render-phase state adjustment (React's documented pattern for
  // resetting state on a prop change), not an effect, so it can't trigger
  // the cascading-render class of bug an effect-based reset would.
  const [lastEdgeId, setLastEdgeId] = useState<number | null>(edge?.id ?? null);
  if ((edge?.id ?? null) !== lastEdgeId) {
    setLastEdgeId(edge?.id ?? null);
    setPreview(null);
    setPreviewError(null);
  }

  const loadPreview = async () => {
    if (mappingId == null || !edge) return;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const data = await api.get<PreviewResponse>(
        `/api/v1/mappings/${mappingId}/edges/${edge.id}/preview`,
      );
      setPreview(data);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Preview failed.");
    } finally {
      setPreviewLoading(false);
    }
  };
  if (!edge) {
    return (
      <div className="p-4 text-xs text-fg-subtle">
        <p className="italic">Select an edge to inspect.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="p-4 border-b border-border">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold">
          Target
        </div>
        <div className="mt-1 text-sm font-mono text-accent">
          {edge.target.table}.{edge.target.column}
        </div>
        <div className="mt-1 text-[11px] text-fg-subtle">
          {edge.target.type ?? "?"}{" "}
          {edge.target.primary_key ? "· PK" : ""}{" "}
          {edge.target.nullable === false ? "· NOT NULL" : ""}
        </div>
      </div>
      <div className="p-4 border-b border-border">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold">
          Source{edge.sources.length > 1 ? "s" : ""} ({edge.sources.length})
        </div>
        <ul className="mt-1 text-xs text-info font-mono space-y-0.5">
          {edge.sources.map((s, i) => (
            <li key={i}>
              {s.table}.{s.column}
              <span className="text-fg-subtle ml-1">({s.type ?? "?"})</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="p-4 border-b border-border">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold flex items-center justify-between">
          <span>Transformation</span>
          <span
            className={classNames(
              "px-1.5 py-0.5 rounded text-[9px] font-bold uppercase",
              edge.origin === "ai_accepted"
                ? "bg-edge-business-rule/15 text-edge-business-rule"
                : "bg-surface-overlay text-fg-subtle",
            )}
          >
            {edge.origin}
            {edge.ai_confidence != null && ` · ${formatPercent(edge.ai_confidence)}`}
          </span>
        </div>
        <pre className="mt-2 text-[11px] font-mono text-fg-muted bg-background/50 rounded p-2 border border-border overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify(edge.transformation, null, 2)}
        </pre>
        {canEdit && (
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              onClick={() => onEdit(edge.transformation)}
              className="flex-1 px-2 py-1.5 text-[11px] font-semibold rounded bg-info/15 text-info border border-info/30 hover:bg-info/25"
              aria-label="Edit transformation"
            >
              ✎ Edit
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="px-2 py-1.5 text-[11px] font-semibold rounded bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25"
              aria-label="Delete edge"
            >
              ✕ Delete
            </button>
          </div>
        )}
      </div>
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold">Preview</span>
          <button
            type="button"
            onClick={() => void loadPreview()}
            disabled={previewLoading || mappingId == null}
            className="px-2 py-1 text-[10px] font-semibold rounded bg-surface-overlay text-fg-muted border border-border-strong hover:bg-surface-overlay/70 disabled:opacity-50"
          >
            {previewLoading ? "Running…" : "▶ Run preview"}
          </button>
        </div>
        {previewError && <p className="mt-2 text-[11px] text-danger">{previewError}</p>}
        {preview && !preview.available && (
          <p className="mt-2 text-[11px] text-warning">{preview.reason}</p>
        )}
        {preview && preview.available && (
          <div className="mt-2 flex flex-col gap-1.5">
            {preview.rows.length === 0 ? (
              <p className="text-[11px] text-fg-subtle italic">No sample rows available.</p>
            ) : (
              preview.rows.map((row, i) => (
                <div key={i} className="rounded border border-border-strong bg-background/40 p-2 text-[11px]">
                  <div className="font-mono text-fg-subtle">
                    {Object.entries(row.source_values).map(([k, v]) => `${k}=${v === null ? "∅" : String(v)}`).join(", ")}
                  </div>
                  <div className="mt-1 text-success font-mono">
                    → {row.output === null || row.output === undefined ? "∅" : String(row.output)}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
      <div className="p-4 text-[10px] text-fg-subtle space-y-1">
        <div>
          <span className="text-fg-subtle">ID:</span> #{edge.id}
        </div>
        {edge.audit.created_by && (
          <div>
            <span className="text-fg-subtle">Created by:</span>{" "}
            {edge.audit.created_by} · {formatTimestamp(edge.audit.created_at)}
          </div>
        )}
        {edge.audit.updated_by && edge.audit.updated_by !== edge.audit.created_by && (
          <div>
            <span className="text-fg-subtle">Updated by:</span>{" "}
            {edge.audit.updated_by} · {formatTimestamp(edge.audit.updated_at)}
          </div>
        )}
        {!canEdit && role && (
          <div className="mt-2 text-warning italic">
            Your role ({role}) cannot edit this edge.
          </div>
        )}
      </div>
    </div>
  );
}
