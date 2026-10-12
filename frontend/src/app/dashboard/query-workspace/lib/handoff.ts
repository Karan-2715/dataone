/**
 * WorkspaceHandoff — cross-page handoff from Schema Intel / Schema Mapper
 * into Query Workspace with pre-filled context.
 *
 * Used via sessionStorage because the source modules (schema, schema-mapper)
 * live on genuinely separate routes and cannot use in-shell callbacks.
 *
 * Design decision #10 in INDEX.md.
 */

export type WorkspaceHandoff = {
  connectionId: number;
  mode: "ask" | "sql";
  sql?: string;
  prefillQuestion?: string;
  banner: { sourceModule: "schema_intel" | "schema_mapper"; summary: string };
};

const HANDOFF_KEY = "query-workspace-handoff";

export function writeWorkspaceHandoff(payload: WorkspaceHandoff): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(payload));
}

export function readAndClearWorkspaceHandoff(): WorkspaceHandoff | null {
  if (typeof window === "undefined") return null;
  const raw = sessionStorage.getItem(HANDOFF_KEY);
  if (!raw) return null;
  sessionStorage.removeItem(HANDOFF_KEY);
  try {
    return JSON.parse(raw) as WorkspaceHandoff;
  } catch {
    return null;
  }
}