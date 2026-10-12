"use client";
import { useState } from "react";
import type { Recommendation, Role } from "../lib/types";
import {
  ConfidenceBadge,
  ReversibleBadge,
  RiskBadge,
  StatusBadge,
} from "./badges";

interface ApprovalQueueProps {
  items: Recommendation[];
  role: Role | null;
  busyId: number | null;
  statusFilter: string;
  onStatusFilter: (status: string) => void;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onModify: (id: number, payload: Record<string, unknown>) => void;
  onEvaluate: () => void;
  evaluating: boolean;
}

const FILTERS = ["pending", "executed", "failed", "rejected", "superseded", "all"];

export default function ApprovalQueue({
  items,
  role,
  busyId,
  statusFilter,
  onStatusFilter,
  onApprove,
  onReject,
  onModify,
  onEvaluate,
  evaluating,
}: ApprovalQueueProps) {
  const isAdmin = role === "admin";
  const canEvaluate = role === "admin" || role === "analyst";
  const [modifyId, setModifyId] = useState<number | null>(null);
  const [modifyText, setModifyText] = useState("");
  const [modifyError, setModifyError] = useState("");

  const startModify = (rec: Recommendation) => {
    setModifyId(rec.id);
    setModifyText(JSON.stringify(rec.payload, null, 2));
    setModifyError("");
  };

  const submitModify = (id: number) => {
    try {
      const parsed = JSON.parse(modifyText) as Record<string, unknown>;
      setModifyError("");
      onModify(id, parsed);
      setModifyId(null);
    } catch {
      setModifyError("Invalid JSON");
    }
  };

  return (
    <section aria-label="Approval queue" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5" role="group" aria-label="Status filter">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => onStatusFilter(f)}
              className={`px-2.5 py-1 text-xs rounded-lg border ${
                statusFilter === f
                  ? "bg-white/10 text-white/80 border-white/20"
                  : "bg-surface text-fg0 border-border hover:text-fg-muted"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        {canEvaluate && (
          <button
            type="button"
            onClick={onEvaluate}
            disabled={evaluating}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-white text-white hover:opacity-90 disabled:opacity-50"
          >
            {evaluating ? "Evaluating…" : "🔎 Evaluate triggers now"}
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <div className="text-xs text-fg0 italic py-6 text-center rounded-xl border border-dashed border-border">
          No {statusFilter === "all" ? "" : `${statusFilter} `}recommendations.
          Autopilot evaluates triggers (schema drift, connector health) every
          few minutes.
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((rec) => (
            <li
              key={rec.id}
              className="rounded-xl border border-border bg-surface-elevated px-4 py-3 flex flex-col gap-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-fg-muted">
                  {rec.action_type}
                </span>
                <span className="font-mono text-xs text-fg0">
                  {rec.subject}
                </span>
                <StatusBadge status={rec.status} />
                <ConfidenceBadge value={rec.confidence} />
                <RiskBadge risk={rec.risk} />
                <ReversibleBadge
                  reversible={rec.reversible}
                  note={rec.reversibility_note}
                />
              </div>
              <p className="text-xs text-fg-subtle leading-relaxed">
                {rec.rationale?.summary ?? "—"}
              </p>
              {rec.reversibility_note && (
                <p className="text-[11px] text-fg0">
                  ↩ {rec.reversibility_note}
                </p>
              )}
              {(rec.rationale?.evidence?.length ?? 0) > 0 && (
                <details className="text-[11px] text-fg0">
                  <summary className="cursor-pointer hover:text-fg-muted">
                    Evidence ({rec.rationale.evidence!.length})
                  </summary>
                  <ul className="mt-1 ml-4 list-disc flex flex-col gap-0.5 font-mono">
                    {rec.rationale.evidence!.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-fg-subtle">
                <span>by {rec.created_by}</span>
                <span>· {new Date(rec.created_at).toLocaleString()}</span>
                {rec.decided_by && (
                  <span>
                    · decided by {rec.decided_by} ({rec.decision_mode})
                  </span>
                )}
                {rec.modified_by && <span>· modified by {rec.modified_by}</span>}
              </div>

              {modifyId === rec.id ? (
                <div className="flex flex-col gap-2">
                  <textarea
                    aria-label="Modify payload JSON"
                    value={modifyText}
                    onChange={(e) => setModifyText(e.target.value)}
                    rows={4}
                    className="font-mono text-xs bg-background border border-border-strong rounded-lg p-2 text-fg-muted"
                  />
                  {modifyError && (
                    <span className="text-xs text-red-400">{modifyError}</span>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => submitModify(rec.id)}
                      className="px-3 py-1 text-xs font-semibold rounded bg-blue-600 hover:bg-blue-500 text-white"
                    >
                      Save payload
                    </button>
                    <button
                      type="button"
                      onClick={() => setModifyId(null)}
                      className="px-3 py-1 text-xs rounded bg-surface-overlay text-fg-subtle border border-border-strong"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                rec.status === "pending" &&
                isAdmin && (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busyId === rec.id}
                      onClick={() => onApprove(rec.id)}
                      className="px-3 py-1 text-xs font-semibold rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 disabled:opacity-50"
                      aria-label={`Approve recommendation ${rec.id}`}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busyId === rec.id}
                      onClick={() => onReject(rec.id)}
                      className="px-3 py-1 text-xs font-semibold rounded bg-surface-overlay text-fg-subtle border border-border-strong hover:bg-surface-overlay disabled:opacity-50"
                      aria-label={`Reject recommendation ${rec.id}`}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      disabled={busyId === rec.id}
                      onClick={() => startModify(rec)}
                      className="px-3 py-1 text-xs font-semibold rounded bg-surface-overlay text-fg-subtle border border-border-strong hover:bg-surface-overlay disabled:opacity-50"
                      aria-label={`Modify recommendation ${rec.id}`}
                    >
                      Modify
                    </button>
                  </div>
                )
              )}
              {rec.execution_result && (
                <details className="text-[11px] text-fg0">
                  <summary className="cursor-pointer hover:text-fg-muted">
                    Execution result
                  </summary>
                  <pre className="mt-1 font-mono text-[10px] bg-background border border-border rounded p-2 overflow-x-auto">
                    {JSON.stringify(rec.execution_result, null, 2)}
                  </pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
