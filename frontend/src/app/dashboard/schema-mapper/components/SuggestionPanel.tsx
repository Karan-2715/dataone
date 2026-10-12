"use client";
import { useRouter } from "next/navigation";
import { classNames, formatPercent, truncate } from "../lib/format";
import type { AISuggestion, Role, TransformationPayload } from "../lib/types";
import { writeWorkspaceHandoff } from "../../query-workspace/lib/handoff";
import { ConfidenceBar } from "../../components";
import { ConfidenceHeatmap } from "./ConfidenceHeatmap";
import FeedbackSummary from "./FeedbackSummary";

const COMPONENT_LABELS: Record<string, string> = {
  name_similarity: "Name similarity",
  type_compatibility: "Type compatibility",
  value_pattern: "Value pattern",
  semantic: "Semantic meaning",
  historical_mapping: "Historical mapping match",
};

// Contributors that are only meaningful once they have a non-zero score —
// showing a permanent "0%" bar for a signal nobody has computed yet reads
// as broken, not honest (E10-7/E14-3 precedent).
const HIDE_WHEN_ZERO = new Set(["value_pattern", "historical_mapping"]);

function normalizedPercent(value: number): number {
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
}

function transformationLabel(transformation: TransformationPayload): string {
  if (transformation.kind === "cast") return `Cast to ${String(transformation.to ?? "target type")}`;
  if (transformation.kind === "coalesce") return "Fill null values";
  if (transformation.kind === "concat") return "Concatenate source values";
  if (transformation.kind === "case") {
    return `If ${transformation.operator} ${transformation.compare_value} then ${transformation.then_value} else ${transformation.else_value}`;
  }
  return transformation.kind.charAt(0).toUpperCase() + transformation.kind.slice(1);
}

interface SuggestionPanelProps {
  pending: AISuggestion[];
  decided: AISuggestion[];
  loading: boolean;
  role: Role | null;
  onRequest: () => void;
  onAccept: (id: number, transformation?: TransformationPayload) => void;
  onReject: (id: number) => void;
  /** The source connection ID for the current mapping — used for investigate handoffs. */
  sourceConnectionId?: number | null;
}

export default function SuggestionPanel({
  pending,
  decided,
  loading,
  role,
  onRequest,
  onAccept,
  onReject,
  sourceConnectionId,
}: SuggestionPanelProps) {
  const router = useRouter();
  const canEdit = role === "admin" || role === "analyst";
  return (
    <section
      aria-label="AI suggestions"
      className="flex-1 flex flex-col overflow-hidden bg-surface-elevated"
    >
      <div className="px-5 py-2.5 flex items-center justify-between shrink-0">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg-muted">AI Suggestions</h3>
          <p className="text-[10px] text-fg-subtle uppercase tracking-wider">
            {pending.length} pending · {decided.length} decided
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            onClick={onRequest}
            disabled={loading}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-white text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Request AI suggestions"
            title={loading ? "AI suggestions are already generating for this mapping" : undefined}
          >
            {loading ? "Generating…" : "  AI Suggest"}
          </button>
        )}
      </div>
      <ConfidenceHeatmap suggestions={[...pending, ...decided]} />
      <FeedbackSummary />
      <div className="flex-1 px-5 pb-3 overflow-y-auto">
        {pending.length === 0 && decided.length === 0 ? (
          <div className="text-xs text-fg-subtle italic py-2">
            No suggestions yet. Click <span className="text-fg-muted">AI Suggest</span> to generate candidates for unmapped target columns.
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {pending.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-edge-business-rule/20 bg-edge-business-rule/5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-mono text-info truncate">
                      {truncate(`${s.source_table}.${s.source_column}`, 30)}
                    </span>
                    <span className="text-fg-subtle">→</span>
                    <span className="font-mono text-accent truncate">
                      {truncate(`${s.target_table}.${s.target_column}`, 30)}
                    </span>
                  </div>
                  {s.reason && (
                    <p className="text-[10px] text-fg-subtle mt-1 truncate">
                      {s.reason}
                    </p>
                  )}
                  {s.components && Object.keys(s.components).length > 0 && (
                    <details className="mt-2 rounded-md border border-border bg-surface-elevated px-2 py-1.5">
                      <summary className="cursor-pointer text-[10px] font-semibold text-edge-business-rule focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded">
                        Why this match?
                      </summary>
                      <div className="mt-2 space-y-2">
                        {Object.entries(s.components ?? {})
                          .filter(([key, value]) => !HIDE_WHEN_ZERO.has(key) || value > 0)
                          .map(([key, value]) => (
                          <ConfidenceBar
                            key={key}
                            value={normalizedPercent(value)}
                            label={COMPONENT_LABELS[key] ?? key.replaceAll("_", " ")}
                            size="sm"
                          />
                        ))}
                      </div>
                    </details>
                  )}
                  {s.suggested_transformation && (
                    <div className="mt-2 rounded-md border border-edge-transformation/20 bg-edge-transformation/5 px-2 py-1.5">
                      <div className="flex items-center gap-2 text-[10px]">
                        <span className="font-semibold uppercase tracking-wide text-edge-transformation">Suggested transform</span>
                        <span className="rounded bg-edge-transformation/15 px-1.5 py-0.5 font-mono text-edge-transformation">
                          {transformationLabel(s.suggested_transformation)}
                        </span>
                      </div>
                      {s.transformation_note && (
                        <p className="mt-1 text-[10px] text-fg-subtle">{s.transformation_note}</p>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-edge-business-rule/15 text-edge-business-rule border border-edge-business-rule/20">
                    {formatPercent(s.confidence)}
                  </span>
                  {canEdit && (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          if (sourceConnectionId == null) return;
                          writeWorkspaceHandoff({
                            connectionId: sourceConnectionId,
                            mode: "sql",
                            sql: `SELECT ${s.source_column}, COUNT(*) FROM ${s.source_table} GROUP BY ${s.source_column} ORDER BY COUNT(*) DESC LIMIT 50;`,
                            banner: { sourceModule: "schema_mapper", summary: `Reviewing suggestion — ${s.source_table}.${s.source_column} → ${s.target_table}.${s.target_column} (${formatPercent(s.confidence)} confidence)` },
                          });
                          router.push("/dashboard/query-workspace");
                        }}
                        className="px-2 py-1 text-[11px] font-semibold rounded bg-info/15 text-info border border-info/30 hover:bg-info/25"
                        aria-label="Investigate suggestion"
                      >
                        Investigate →
                      </button>
                      <button
                        type="button"
                        onClick={() => onAccept(s.id, s.suggested_transformation ?? undefined)}
                        className="px-2 py-1 text-[11px] font-semibold rounded bg-success/15 text-success border border-success/30 hover:bg-success/25"
                        aria-label="Accept suggestion"
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        onClick={() => onReject(s.id)}
                        className="px-2 py-1 text-[11px] font-semibold rounded bg-surface-overlay text-fg-subtle border border-border-strong hover:bg-surface-overlay"
                        aria-label="Reject suggestion"
                      >
                        Reject
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
            {decided.length > 0 && (
              <li className="mt-2 mb-1 text-[10px] uppercase tracking-wider text-fg-subtle font-semibold">
                Decided
              </li>
            )}
            {decided.slice(0, 8).map((s) => (
              <li
                key={s.id}
                className={classNames(
                  "flex items-center justify-between gap-2 px-3 py-1.5 rounded text-[11px]",
                  s.status === "accepted"
                    ? "bg-success/5 text-success/80"
                    : "bg-surface-elevated text-fg-subtle",
                )}
              >
                <span className="font-mono truncate">
                  {truncate(`${s.source_table}.${s.source_column}`, 28)} → {truncate(`${s.target_table}.${s.target_column}`, 28)}
                </span>
                <span className="text-[10px] uppercase font-semibold shrink-0">
                  {s.status} · {formatPercent(s.confidence)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
