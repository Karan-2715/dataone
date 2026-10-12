"use client";
import type { ActionLogEntry } from "../lib/types";
import { OutcomeBadge } from "./badges";

export default function ActionLog({ items }: { items: ActionLogEntry[] }) {
  if (items.length === 0) {
    return (
      <div className="text-xs text-fg0 italic py-6 text-center rounded-xl border border-dashed border-border">
        No actions yet. Every execution attempt and guardrail block lands here
        with its outcome and reversibility note.
      </div>
    );
  }
  return (
    <ul aria-label="Action log" className="flex flex-col gap-2">
      {items.map((a) => (
        <li
          key={a.id}
          className="rounded-xl border border-border bg-surface-elevated px-4 py-2.5 flex flex-col gap-1.5"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-fg-muted">{a.action_type}</span>
            <OutcomeBadge outcome={a.outcome} />
            <span
              className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border ${
                a.mode === "auto"
                  ? "bg-white/5 text-white/80 border-white/10"
                  : "bg-blue-500/10 text-blue-300 border-blue-500/25"
              }`}
            >
              {a.mode}
            </span>
            {a.recommendation_id != null && (
              <span className="text-[10px] text-fg-subtle font-mono">
                rec #{a.recommendation_id}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px] text-fg-subtle">
            <span>{a.actor}</span>
            <span>· {new Date(a.started_at).toLocaleString()}</span>
          </div>
          {a.reversibility_note && (
            <p className="text-[11px] text-fg0">↩ {a.reversibility_note}</p>
          )}
          {a.detail && (
            <details className="text-[11px] text-fg0">
              <summary className="cursor-pointer hover:text-fg-muted">Detail</summary>
              <pre className="mt-1 font-mono text-[10px] bg-background border border-border rounded p-2 overflow-x-auto">
                {JSON.stringify(a.detail, null, 2)}
              </pre>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
