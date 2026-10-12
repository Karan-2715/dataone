"use client";
import { api } from "@/lib/api";
import { useWidgetData } from "../../hooks/useWidgetData";

interface FeedbackSummaryItem {
  source_column: string;
  target_column: string;
  accepted_count: number;
  rejected_count: number;
}

interface FeedbackSummaryData {
  total_decided: number;
  total_accepted: number;
  total_rejected: number;
  acceptance_rate: number;
  most_corrected: FeedbackSummaryItem[];
}

/**
 * AI Mapping Feedback & Training Loop summary (Enterprise v2, E14-5).
 * Reads the live `ai_suggestions` accept/reject history platform-wide —
 * heuristic memory, not a trained model, so there is no separate "last
 * trained at" timestamp to show; this is always current.
 */
export default function FeedbackSummary() {
  const { data, isLoading, isError } = useWidgetData<FeedbackSummaryData>(
    (signal) => api.get("/api/v1/mappings/feedback-summary", { signal }),
    [],
  );

  if (isLoading || isError || !data) return null;

  if (data.total_decided === 0) {
    return (
      <p className="px-5 pb-2 text-[10px] text-fg-subtle italic">
        No AI-suggestion feedback recorded yet — accept/reject decisions across mappings will inform future ranking.
      </p>
    );
  }

  return (
    <details className="mx-5 mb-2 rounded-md border border-border bg-surface-elevated px-2 py-1.5">
      <summary className="cursor-pointer text-[10px] font-semibold text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded">
        AI feedback: {data.acceptance_rate}% acceptance rate ({data.total_decided} decided)
      </summary>
      {data.most_corrected.length > 0 && (
        <ul className="mt-2 space-y-1 text-[10px] text-fg-subtle">
          {data.most_corrected.map((item) => (
            <li key={`${item.source_column}->${item.target_column}`} className="font-mono">
              {item.source_column} → {item.target_column}: {item.rejected_count} rejected
              {item.accepted_count > 0 ? ` / ${item.accepted_count} accepted` : ""}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
