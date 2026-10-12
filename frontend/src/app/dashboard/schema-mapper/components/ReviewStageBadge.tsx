"use client";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ReviewStage, Role } from "../lib/types";

interface ReviewStageBadgeProps {
  mappingId: number;
  reviewStage: ReviewStage;
  role: Role | null;
  onTransitioned: () => void | Promise<void>;
}

export const STAGE_LABELS: Record<ReviewStage, string> = {
  draft: "Draft",
  ai_proposed: "AI Proposed",
  pending_review: "Pending Review",
  business_approved: "Business Approved",
  steward_approved: "Data Steward Approved",
  production_ready: "Production Ready",
  rejected: "Rejected",
};

export const STAGE_COLORS: Record<ReviewStage, string> = {
  draft: "bg-surface-overlay text-fg-subtle border-border-strong",
  ai_proposed: "bg-edge-business-rule/15 text-edge-business-rule border-edge-business-rule/30",
  pending_review: "bg-warning/15 text-warning border-warning/30",
  business_approved: "bg-info/15 text-info border-info/30",
  steward_approved: "bg-accent-soft text-accent border-accent/30",
  production_ready: "bg-success/15 text-success border-success/30",
  rejected: "bg-danger/15 text-danger border-danger/30",
};

// Mirrors mapping_review_service.py's _TRANSITIONS map — the frontend only
// offers a transition button when it's plausible; the backend remains the
// sole authority on whether it's actually allowed for this role/stage.
const NEXT_STAGE: Partial<Record<ReviewStage, ReviewStage>> = {
  draft: "pending_review",
  ai_proposed: "pending_review",
  pending_review: "business_approved",
  business_approved: "steward_approved",
  steward_approved: "production_ready",
  rejected: "draft",
  // build-validation B-E13-08: without this, a production mapping needing
  // a revision (e.g. a source column type changed) had no way back into
  // the workflow at all.
  production_ready: "draft",
};

export default function ReviewStageBadge({ mappingId, reviewStage, role, onTransitioned }: ReviewStageBadgeProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const next = NEXT_STAGE[reviewStage];
  const canEdit = role === "admin" || role === "analyst";

  const transition = async (toStage: ReviewStage, note?: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/mappings/${mappingId}/review/transition`, { to_stage: toStage, note });
      await onTransitioned();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Transition failed.");
    } finally {
      setBusy(false);
    }
  };

  const rejectWithOptionalNote = () => {
    // A note here becomes a steward comment tied to this exact rejection
    // (E16-3) — a native prompt is a deliberately small v1 surface, not a
    // full comment composer, since rejection is the one transition where
    // "why" is almost always worth capturing.
    const note = window.prompt("Optional note for this rejection (visible to the team as a steward comment):");
    if (note === null) return; // cancelled
    void transition("rejected", note.trim() || undefined);
  };

  return (
    <div className="flex items-center gap-2">
      <span
        className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${STAGE_COLORS[reviewStage]}`}
        title={`Review stage: ${STAGE_LABELS[reviewStage]}`}
      >
        {STAGE_LABELS[reviewStage]}
      </span>
      {canEdit && next && (
        <button
          type="button"
          onClick={() => void transition(next)}
          disabled={busy}
          className="px-2 py-0.5 text-[10px] font-semibold rounded bg-surface-overlay text-fg-muted border border-border-strong hover:bg-surface-overlay/70 disabled:opacity-50"
        >
          {busy ? "…" : `→ ${STAGE_LABELS[next]}`}
        </button>
      )}
      {canEdit && reviewStage !== "rejected" && reviewStage !== "production_ready" && reviewStage !== "draft" && (
        <button
          type="button"
          onClick={rejectWithOptionalNote}
          disabled={busy}
          className="px-2 py-0.5 text-[10px] font-semibold rounded bg-danger/10 text-danger border border-danger/30 hover:bg-danger/20 disabled:opacity-50"
        >
          Reject
        </button>
      )}
      {error && <span className="text-[10px] text-danger">{error}</span>}
    </div>
  );
}
