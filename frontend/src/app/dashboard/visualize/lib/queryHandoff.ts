/**
 * VisualizeQueryHandoff — cross-page handoff from Query Workspace into
 * Visualize, carrying the current page of a SQL result so it can be
 * charted without a server round-trip.
 *
 * Mirrors the query-workspace/lib/handoff.ts pattern (sessionStorage,
 * one-shot write/read-and-clear) since these are separate routes.
 */

export type VisualizeQueryHandoff = {
  connectionId: number;
  sql: string;
  columns: string[];
  rows: Record<string, unknown>[];
};

const HANDOFF_KEY = "visualize-query-handoff";

export function writeVisualizeQueryHandoff(payload: VisualizeQueryHandoff): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(payload));
}

export function readAndClearVisualizeQueryHandoff(): VisualizeQueryHandoff | null {
  if (typeof window === "undefined") return null;
  const raw = sessionStorage.getItem(HANDOFF_KEY);
  if (!raw) return null;
  sessionStorage.removeItem(HANDOFF_KEY);
  try {
    return JSON.parse(raw) as VisualizeQueryHandoff;
  } catch {
    return null;
  }
}
