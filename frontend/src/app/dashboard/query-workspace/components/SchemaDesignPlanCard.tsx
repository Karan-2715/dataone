"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { SchemaDesignPlan } from "../../askdata/lib/types";

const POLL_MS = 2500;

const STATUS_META: Record<string, { label: string; cls: string }> = {
  generating: { label: "Generating plan…", cls: "text-blue-300 bg-blue-500/10 border-blue-500/30" },
  ready: { label: "Ready for review", cls: "text-white/80 bg-white/5 border-white/20" },
  failed: { label: "Failed", cls: "text-red-300 bg-red-500/10 border-red-500/30" },
  rejected: { label: "Rejected", cls: "text-fg-subtle bg-surface-overlay border-border-strong/30" },
  applying: { label: "Applying…", cls: "text-amber-300 bg-amber-500/10 border-amber-500/30" },
  applied: { label: "Applied", cls: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30" },
  partially_applied: { label: "Partially applied", cls: "text-amber-300 bg-amber-500/10 border-amber-500/30" },
};

function Section({ title, count, children, defaultOpen = false }: {
  title: string; count: number; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (count === 0) return null;
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-surface-elevated text-left"
      >
        <span className="text-[11px] font-semibold text-fg-muted">{title}</span>
        <span className="text-[10px] text-fg0">{count} · {open ? "▾" : "▸"}</span>
      </button>
      {open && <div className="p-2">{children}</div>}
    </div>
  );
}

export default function SchemaDesignPlanCard({ planId }: { planId: number }) {
  const [plan, setPlan] = useState<SchemaDesignPlan | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [acting, setActing] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPlan = useCallback(async () => {
    try {
      const p = await api.get<SchemaDesignPlan>(`/api/v1/agentic-dba/plans/${planId}`);
      setPlan(p);
      return p;
    } catch {
      return null;
    }
  }, [planId]);

  // Poll while the plan is still generating/applying (task #3's async
  // contract) — stop on any settled status.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const p = await fetchPlan();
      if (cancelled) return;
      if (p && p.status !== "generating" && p.status !== "applying" && timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
    tick();
    timerRef.current = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchPlan]);

  const act = async (verb: "approve" | "reject") => {
    setActing(true);
    setActionError(null);
    try {
      const p = await api.post<SchemaDesignPlan>(
        `/api/v1/agentic-dba/plans/${planId}/${verb}`, {});
      setPlan(p);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : `Could not ${verb} the plan.`);
    } finally {
      setActing(false);
      setConfirming(false);
    }
  };

  if (!plan) {
    return (
      <div className="mt-2 rounded-xl border border-border bg-surface-elevated px-3 py-2 text-[11px] text-fg0">
        Loading design plan…
      </div>
    );
  }

  const meta = STATUS_META[plan.status] ?? STATUS_META.generating;
  const tables = plan.proposed_tables ?? [];
  const dqRules = plan.dq_rules ?? [];
  const transforms = plan.transformations ?? [];
  const ddl = plan.generated_ddl ?? [];
  const notes = plan.confidence_notes ?? [];
  const applyResults = plan.apply_results ?? [];
  const reviewable = plan.status === "ready";

  return (
    <div className="mt-2 rounded-xl border border-white/10 bg-surface-elevated p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold text-white/80">  Schema Design Plan #{plan.id}</span>
        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${meta.cls}`}>
          {meta.label}
        </span>
        {plan.domain_template && (
          <span className="text-[9px] text-fg0">template: {plan.domain_template}</span>
        )}
      </div>

      {plan.status === "generating" && (
        <div className="flex items-center gap-2 text-[11px] text-fg-subtle">
          <div className="w-2 h-2 bg-white rounded-full animate-pulse" />
          Grounding in the Schema Intel catalog and profiling stats…
        </div>
      )}

      {plan.status === "failed" && plan.error && (
        <div className="text-[11px] text-red-300">{plan.error}</div>
      )}

      {tables.length > 0 && (
        <Section title="Proposed tables" count={tables.length} defaultOpen>
          <div className="flex flex-col gap-2">
            {tables.map((t) => (
              <div key={t.name} className="rounded-lg bg-background/50 border border-border/60 p-2">
                <div className="text-[11px] font-mono font-semibold text-fg-muted">{t.name}</div>
                <table className="w-full text-[10px] mt-1">
                  <tbody>
                    {t.columns.map((c) => (
                      <tr key={c.name} className="border-t border-border/40">
                        <td className="py-0.5 pr-2 font-mono text-fg-muted">
                          {c.name}{c.primary_key ? " 🔑" : ""}{c.nullable === false && !c.primary_key ? " *" : ""}
                        </td>
                        <td className="py-0.5 pr-2 text-fg0">{c.type}</td>
                        <td className="py-0.5 text-fg-subtle">
                          {c.source_refs.length > 0
                            ? `← ${c.source_refs.map((s) => `${s.table}.${s.column}`).join(" + ")}`
                            : "new"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Data quality rules (profiling-justified)" count={dqRules.length}>
        <ul className="flex flex-col gap-1.5">
          {dqRules.map((r, i) => (
            <li key={i} className="text-[10px]">
              <span className="font-mono text-white/80 uppercase">{r.rule}</span>{" "}
              <span className="font-mono text-fg-muted">{r.target_table}.{r.target_column}</span>
              <span className="text-fg0"> — {r.justification}</span>{" "}
              <span className="text-fg-subtle">({Math.round(r.confidence * 100)}% confidence)</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Transformations" count={transforms.length}>
        <ul className="flex flex-col gap-1.5">
          {transforms.map((t, i) => (
            <li key={i} className="text-[10px]">
              <span className="font-mono text-fg-muted">
                {t.sources.map((s) => `${s.table}.${s.column}`).join(" + ")} → {t.target_table}.{t.target_column}
              </span>{" "}
              {t.transformation ? (
                <span className="text-fg font-mono">[{String(t.transformation.kind)}]</span>
              ) : (
                <span className="text-fg-subtle">needs manual authoring</span>
              )}
              {t.note && <span className="text-fg-subtle"> — {t.note}</span>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Generated DDL" count={ddl.length}>
        <div className="flex flex-col gap-1.5">
          {ddl.map((d) => (
            <div key={d.table}>
              <div className="text-[10px] text-fg-subtle">
                <span className="font-mono">{d.table}</span>{" "}
                {d.mode === "migrate" && (
                  <span className="text-amber-400">— already exists: ALTER-based migration proposed</span>
                )}
              </div>
              <pre className="mt-0.5 text-[10px] font-mono text-fg-muted bg-surface-elevated border border-border rounded p-2 overflow-x-auto whitespace-pre-wrap">
                {d.statements.join("\n")}
              </pre>
            </div>
          ))}
        </div>
      </Section>

      {notes.length > 0 && (
        <Section title="Caveats & confidence notes" count={notes.length}>
          <ul className="list-disc pl-4 flex flex-col gap-1">
            {notes.map((n, i) => (
              <li key={i} className="text-[10px] text-fg0">{n}</li>
            ))}
          </ul>
        </Section>
      )}

      {applyResults.length > 0 && (
        <Section title="Apply results (per object)" count={applyResults.length} defaultOpen>
          <ul className="flex flex-col gap-1">
            {applyResults.map((r) => (
              <li key={r.table} className="text-[10px] font-mono">
                <span className={
                  r.status === "applied" ? "text-emerald-400"
                    : r.status === "failed" ? "text-red-400" : "text-fg0"
                }>
                  {r.status === "applied" ? "✓" : r.status === "failed" ? "✗" : "○"} {r.table} — {r.status}
                </span>
                {r.error && <span className="text-red-300/70"> ({r.error})</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {plan.created_mapping_id != null && (
        <div className="text-[10px] text-emerald-300">
          Draft mapping #{plan.created_mapping_id} created — review it in Schema Mapper.
        </div>
      )}

      {actionError && <div className="text-[10px] text-red-300">{actionError}</div>}

      <div className="flex gap-2 pt-1">
        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            disabled={!reviewable || acting}
            className="px-3 py-1.5 text-[11px] font-semibold rounded-lg bg-fg text-background disabled:opacity-40"
          >
            Approve &amp; Create
          </button>
        ) : (
          <button
            onClick={() => act("approve")}
            disabled={acting}
            className="px-3 py-1.5 text-[11px] font-semibold rounded-lg bg-red-600 text-white disabled:opacity-40"
          >
            {acting ? "Applying…" : `Confirm: create/alter ${ddl.length} table(s)`}
          </button>
        )}
        {confirming && (
          <button
            onClick={() => setConfirming(false)}
            disabled={acting}
            className="px-3 py-1.5 text-[11px] rounded-lg bg-surface-overlay text-fg-muted"
          >
            Back
          </button>
        )}
        <button
          onClick={() => act("reject")}
          disabled={!reviewable || acting}
          className="px-3 py-1.5 text-[11px] rounded-lg bg-surface-overlay text-fg-subtle hover:text-fg-muted disabled:opacity-40"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
