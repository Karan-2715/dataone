"use client";
import { useEffect, useRef, useState } from "react";
import { classNames } from "../lib/format";
import type { Mapping, Role, ValidationResponse } from "../lib/types";
import ReportButtons from "./ReportButtons";
import ReviewStageBadge from "./ReviewStageBadge";

interface WorkspaceHeaderProps {
  mapping: Mapping;
  role: Role | null;
  validation: ValidationResponse | null;
  onValidate: () => void;
  onPublish: () => void;
  onExport: () => void;
  onRename: (name: string) => Promise<void>;
  onReviewTransitioned: () => void | Promise<void>;
  onRevise: () => Promise<void>;
  validating: boolean;
  publishing: boolean;
  /** Focus mode hides the mapping list rail and side panels so the canvas
   * gets more room (uiux bug report: "canvas is so small"). */
  focusMode: boolean;
  onToggleFocusMode: () => void;
}

export default function WorkspaceHeader({
  mapping,
  role,
  validation,
  onValidate,
  onPublish,
  onExport,
  onRename,
  onReviewTransitioned,
  onRevise,
  validating,
  publishing,
  focusMode,
  onToggleFocusMode,
}: WorkspaceHeaderProps) {
  const isDraft = mapping.status === "draft";
  const canEdit = isDraft && (role === "admin" || role === "analyst");
  const canPublish = canEdit && role === "admin";
  const canRevise = !isDraft && (role === "admin" || role === "analyst");
  const [revising, setRevising] = useState(false);
  const blocking = validation?.blocking_count ?? 0;
  const warnings = validation?.warning_count ?? 0;

  const handleRevise = async () => {
    setRevising(true);
    try {
      await onRevise();
    } catch {
      // onRevise already toasted the error.
    } finally {
      setRevising(false);
    }
  };

  // Inline-rename UI (TRD FR8 implied; mapper_tasks #6).
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(mapping.name);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Keep the draft in sync if the mapping changes externally (e.g. switched
  // mappings in the sidebar list) — a render-phase state adjustment
  // (React's documented pattern for resetting state on a prop change)
  // rather than an effect, so it can't trigger a cascading-render.
  const syncKey = `${mapping.name}|${editing}`;
  const [lastSyncKey, setLastSyncKey] = useState(syncKey);
  if (syncKey !== lastSyncKey) {
    setLastSyncKey(syncKey);
    if (!editing) setDraftName(mapping.name);
  }

  // Focus the input when entering edit mode.
  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const startEdit = () => {
    if (!canEdit) return;
    setDraftName(mapping.name);
    setEditing(true);
  };

  const cancelEdit = () => {
    setDraftName(mapping.name);
    setEditing(false);
  };

  const commitEdit = async () => {
    const trimmed = draftName.trim();
    if (!trimmed || trimmed === mapping.name) {
      cancelEdit();
      return;
    }
    try {
      await onRename(trimmed);
      setEditing(false);
    } catch {
      // onRename already toasted the error; leave the editor open so the
      // user can fix and retry.
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-glass-bg px-5 py-3 backdrop-blur-sm">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {editing ? (
            <input
              ref={inputRef}
              type="text"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void commitEdit();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
              aria-label="Rename mapping"
              className="min-w-0 max-w-md rounded border border-accent/40 bg-surface-overlay px-2 py-0.5 text-base font-semibold text-fg focus:border-accent focus:outline-none"
            />
          ) : (
            <>
              {/* `truncate` must sit on a block element, not a flex
                  container — text-overflow doesn't apply to flex, so the
                  previous flex h2 hard-clipped long names with no ellipsis.
                  The ✎ button is a sibling, not heading content, so screen
                  readers don't read it as part of the heading
                  (review_schema_mapper_round2 #9). */}
              <h2 className="text-base font-semibold text-fg truncate min-w-0">
                {mapping.name}
              </h2>
              {canEdit && (
                <button
                  type="button"
                  onClick={startEdit}
                  aria-label="Rename mapping"
                  title="Rename mapping"
                  className="shrink-0 text-xs text-fg-subtle transition-colors hover:text-accent"
                >
                  ✎
                </button>
              )}
            </>
          )}
          <span
            className={classNames(
              "px-2 py-0.5 rounded text-[10px] font-bold uppercase",
              isDraft
                ? "bg-warning/10 text-warning border border-warning/20"
                : "bg-success/10 text-success border border-success/20",
            )}
          >
            {mapping.status}
            {mapping.current_version_id && isDraft === false
              ? ` v${mapping.current_version_id}`
              : ""}
          </span>
          <ReviewStageBadge
            mappingId={mapping.id}
            reviewStage={mapping.review_stage}
            role={role}
            onTransitioned={onReviewTransitioned}
          />
          {blocking > 0 && (
            <span className="rounded border border-danger/20 bg-danger/10 px-2 py-0.5 text-[10px] font-bold uppercase text-danger">
              {blocking} blocking
            </span>
          )}
          {warnings > 0 && blocking === 0 && (
            <span className="rounded border border-warning/20 bg-warning/10 px-2 py-0.5 text-[10px] font-bold uppercase text-warning">
              {warnings} warning{warnings === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[11px] text-fg-subtle">
          #{mapping.id} · {mapping.edges.length} edge
          {mapping.edges.length === 1 ? "" : "s"} · created by {mapping.created_by}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onToggleFocusMode}
          aria-pressed={focusMode}
          title={focusMode ? "Restore side panels" : "Hide side panels for more canvas room"}
          className="rounded-lg border border-border bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent"
          aria-label={focusMode ? "Exit focus mode" : "Enter focus mode"}
        >
          {focusMode ? "⤢ Exit focus" : "⛶ Focus canvas"}
        </button>
        <button
          type="button"
          onClick={onValidate}
          disabled={validating}
          className="rounded-lg border border-border bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
          aria-label="Validate mapping"
        >
          {validating ? "Validating…" : "✓ Validate"}
        </button>
        <button
          type="button"
          onClick={onExport}
          className="rounded-lg border border-border bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent"
          aria-label="Export published mapping"
        >
          ⬇ Export
        </button>
        <ReportButtons mappingId={mapping.id} />
        {canRevise && (
          <button
            type="button"
            onClick={() => void handleRevise()}
            disabled={revising}
            title="Reopen this published mapping for editing (starts a new draft from the current version)"
            className="rounded-lg border border-border bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
            aria-label="Revise mapping"
          >
            {revising ? "Reopening…" : "↺ Revise"}
          </button>
        )}
        {canPublish && (
          <button
            type="button"
            onClick={onPublish}
            disabled={publishing || blocking > 0}
            title={
              blocking > 0
                ? `Resolve ${blocking} blocking issue(s) before publishing`
                : isDraft
                  ? "Publish a new immutable version"
                  : "Already published"
            }
            className="workspace-primary-action min-h-0 rounded-lg px-3 py-1.5 text-xs disabled:cursor-not-allowed"
            aria-label="Publish mapping"
          >
            {publishing ? "Publishing…" : "🚀 Publish"}
          </button>
        )}
        {!canEdit && role !== null && (
          <span
            className="text-[11px] italic text-fg-subtle"
            title="Your role cannot edit this mapping."
          >
            Read-only ({role})
          </span>
        )}
      </div>
    </div>
  );
}
