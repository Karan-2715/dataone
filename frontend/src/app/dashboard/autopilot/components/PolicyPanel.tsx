"use client";
import { useState } from "react";
import type { AutopilotPolicyEntry, Role } from "../lib/types";
import { ReversibleBadge, RiskBadge } from "./badges";

const LEVELS: Array<AutopilotPolicyEntry["autonomy"]> = [
  "disabled",
  "suggest",
  "approve",
  "auto",
];

interface PolicyPanelProps {
  policies: AutopilotPolicyEntry[];
  role: Role | null;
  savingType: string | null;
  onSave: (
    actionType: string,
    autonomy: AutopilotPolicyEntry["autonomy"],
    maxAutoPerHour: number,
  ) => void;
}

export default function PolicyPanel({
  policies,
  role,
  savingType,
  onSave,
}: PolicyPanelProps) {
  const isAdmin = role === "admin";
  // Local edits keyed by action_type; row is dirty when it differs from props.
  const [edits, setEdits] = useState<
    Record<string, { autonomy: AutopilotPolicyEntry["autonomy"]; max: number }>
  >({});

  const rowState = (p: AutopilotPolicyEntry) =>
    edits[p.action_type] ?? { autonomy: p.autonomy, max: p.max_auto_per_hour };

  return (
    <section aria-label="Autonomy policy" className="flex flex-col gap-3">
      <p className="text-xs text-fg0 leading-relaxed">
        Per action type: how far Autopilot may go.{" "}
        <span className="text-fg-subtle">suggest</span> = recommend only,{" "}
        <span className="text-fg-subtle">approve</span> = queue for human approval,{" "}
        <span className="text-fg-subtle">auto</span> = execute autonomously within
        limits (only reversible, low-risk actions qualify). Destructive and
        security-related actions are hard-blocked server-side regardless of this
        configuration.
        {!isAdmin && (
          <span className="block mt-1 text-amber-400/80">
            Read-only — policy changes require the admin role.
          </span>
        )}
      </p>
      <ul className="flex flex-col gap-2">
        {policies.map((p) => {
          const st = rowState(p);
          const dirty =
            st.autonomy !== p.autonomy || st.max !== p.max_auto_per_hour;
          return (
            <li
              key={p.action_type}
              className="rounded-xl border border-border bg-surface-elevated px-4 py-3 flex flex-col gap-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-fg-muted">
                  {p.action_type}
                </span>
                <RiskBadge risk={p.risk} />
                <ReversibleBadge
                  reversible={p.reversible}
                  note={p.reversibility_note}
                />
              </div>
              <p className="text-xs text-fg0">{p.description}</p>
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-xs text-fg0">
                  Autonomy
                  <select
                    aria-label={`Autonomy for ${p.action_type}`}
                    value={st.autonomy}
                    disabled={!isAdmin}
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [p.action_type]: {
                          ...st,
                          autonomy: e.target
                            .value as AutopilotPolicyEntry["autonomy"],
                        },
                      }))
                    }
                    className="bg-surface-overlay border border-border-strong text-fg-muted text-xs rounded-lg px-2 py-1.5 disabled:opacity-50"
                  >
                    {LEVELS.map((lvl) => (
                      <option
                        key={lvl}
                        value={lvl}
                        disabled={lvl === "auto" && !p.auto_capable}
                      >
                        {lvl}
                        {lvl === "auto" && !p.auto_capable
                          ? " (not allowed — irreversible/high risk)"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-xs text-fg0">
                  Max auto/hour
                  <input
                    type="number"
                    min={0}
                    aria-label={`Max auto per hour for ${p.action_type}`}
                    value={st.max}
                    disabled={!isAdmin}
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [p.action_type]: {
                          ...st,
                          max: Number(e.target.value),
                        },
                      }))
                    }
                    className="w-20 bg-surface-overlay border border-border-strong text-fg-muted text-xs rounded-lg px-2 py-1.5 disabled:opacity-50"
                  />
                </label>
                {isAdmin && dirty && (
                  <button
                    type="button"
                    disabled={savingType === p.action_type}
                    onClick={() => onSave(p.action_type, st.autonomy, st.max)}
                    className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                  >
                    {savingType === p.action_type ? "Saving…" : "Save"}
                  </button>
                )}
                {p.updated_by && (
                  <span className="text-[10px] text-fg-subtle">
                    last changed by {p.updated_by}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
