"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { classNames, formatTimestamp } from "../lib/format";
import { useWidgetData } from "../../hooks/useWidgetData";
import type { Role } from "../lib/types";

interface Annotation {
  id: number;
  mapping_id: number;
  edge_id: number | null;
  parent_id: number | null;
  kind: "comment" | "steward_comment";
  review_stage: string | null;
  author: string;
  body: string;
  created_at: string;
}

interface CommentsPanelProps {
  mappingId: number;
  role: Role | null;
  currentUserEmail: string | null;
}

/**
 * Async collaboration comments (Enterprise v2, E16-1/2). Mapping-level
 * only in this v1 — no per-edge pinning UI yet, no presence, no realtime
 * (E16-4/5/6 need a transport + security decision, see the E16 spec).
 * Steward comments (created only by a review-stage transition note) are
 * visually distinguished but not separately composable here.
 */
export default function CommentsPanel({ mappingId, role, currentUserEmail }: CommentsPanelProps) {
  const { data, isLoading, isError, refetch } = useWidgetData<Annotation[]>(
    (signal) => api.get(`/api/v1/mappings/${mappingId}/annotations`, { signal }),
    [mappingId],
  );
  const [draft, setDraft] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canDelete = (a: Annotation) => role === "admin" || a.author === currentUserEmail;

  const submit = async () => {
    const body = draft.trim();
    if (!body) return;
    setPosting(true);
    setError(null);
    try {
      await api.post(`/api/v1/mappings/${mappingId}/annotations`, { body });
      setDraft("");
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to post comment.");
    } finally {
      setPosting(false);
    }
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await api.delete(`/api/v1/mappings/${mappingId}/annotations/${id}`);
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete comment.");
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {isLoading && <p className="text-fg-subtle italic">Loading comments…</p>}
        {isError && <p className="text-danger">Failed to load comments.</p>}
        {!isLoading && !isError && (data?.length ?? 0) === 0 && (
          <p className="text-fg-subtle italic">No comments yet.</p>
        )}
        {data?.map((a) => (
          <div
            key={a.id}
            className={classNames(
              "rounded-md border px-2 py-1.5",
              a.kind === "steward_comment"
                ? "border-accent/30 bg-accent-soft"
                : "border-border bg-surface-overlay",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-fg-muted truncate">
                {a.author}
                {a.kind === "steward_comment" && (
                  <span className="ml-1.5 rounded bg-accent-soft px-1 py-0.5 text-[9px] font-bold uppercase text-accent">
                    Steward · {a.review_stage}
                  </span>
                )}
              </span>
              {canDelete(a) && (
                <button
                  type="button"
                  onClick={() => void remove(a.id)}
                  aria-label={`Delete comment ${a.id}`}
                  className="shrink-0 text-fg-subtle hover:text-danger"
                >
                  ✕
                </button>
              )}
            </div>
            <p className="mt-1 text-fg-subtle whitespace-pre-wrap break-words">{a.body}</p>
            <p className="mt-1 text-[9px] text-fg-subtle">{formatTimestamp(a.created_at)}</p>
          </div>
        ))}
      </div>
      <div className="border-t border-border p-2 shrink-0">
        {error && <p className="mb-1 text-danger">{error}</p>}
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add a comment…"
          rows={2}
          aria-label="New comment"
          className="w-full rounded border border-border-strong bg-surface-overlay px-2 py-1 text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent/50"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={posting || !draft.trim()}
          className="mt-1 w-full rounded bg-info/15 px-2 py-1 font-semibold text-info border border-info/30 hover:bg-info/25 disabled:opacity-50"
        >
          {posting ? "Posting…" : "Post comment"}
        </button>
      </div>
    </div>
  );
}
