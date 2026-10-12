import type { ReactElement } from "react";

export function RiskBadge({ risk }: { risk: "low" | "medium" | "high" }): ReactElement {
  const styles =
    risk === "low"
      ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/25"
      : risk === "medium"
        ? "bg-amber-500/10 text-amber-300 border-amber-500/25"
        : "bg-red-500/10 text-red-300 border-red-500/25";
  return (
    <span className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border ${styles}`}>
      {risk} risk
    </span>
  );
}

export function ReversibleBadge({
  reversible,
  note,
}: {
  reversible: boolean;
  note?: string | null;
}): ReactElement {
  return (
    <span
      title={note ?? undefined}
      className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border ${
        reversible
          ? "bg-blue-500/10 text-blue-300 border-blue-500/25"
          : "bg-red-500/10 text-red-300 border-red-500/25"
      }`}
    >
      {reversible ? "reversible" : "irreversible"}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: number }): ReactElement {
  return (
    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-white/10 text-white/80 border border-white/10">
      {Math.round(value)}%
    </span>
  );
}

const OUTCOME_STYLES: Record<string, string> = {
  success: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  failure: "bg-red-500/10 text-red-300 border-red-500/25",
  blocked_prohibited: "bg-red-500/15 text-red-300 border-red-500/30",
  blocked_rate_limit: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  blocked_breaker: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  blocked_policy: "bg-surface-overlay text-fg-muted border-border-strong",
};

export function OutcomeBadge({ outcome }: { outcome: string }): ReactElement {
  return (
    <span
      className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border ${
        OUTCOME_STYLES[outcome] ?? "bg-surface-overlay text-fg-subtle border-border-strong"
      }`}
    >
      {outcome.replace(/_/g, " ")}
    </span>
  );
}

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-white/5 text-white/80 border-white/10",
  approved: "bg-blue-500/10 text-blue-300 border-blue-500/25",
  executing: "bg-blue-500/10 text-blue-300 border-blue-500/25",
  executed: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  rejected: "bg-surface-overlay text-fg-subtle border-border-strong",
  superseded: "bg-surface-overlay text-fg-subtle border-border-strong",
  failed: "bg-red-500/10 text-red-300 border-red-500/25",
};

export function StatusBadge({ status }: { status: string }): ReactElement {
  return (
    <span
      className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border ${
        STATUS_STYLES[status] ?? "bg-surface-overlay text-fg-subtle border-border-strong"
      }`}
    >
      {status}
    </span>
  );
}
