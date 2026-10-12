"use client";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { classNames, formatTimestamp } from "../lib/format";
import { useWidgetData } from "../../hooks/useWidgetData";
import type {
  EdgeSnapshot,
  Role,
  VersionDiffResponse,
  VersionListResponse,
} from "../lib/types";

interface VersionHistoryPanelProps {
  mappingId: number;
  role: Role | null;
  onRolledBack: () => void | Promise<void>;
}

function edgeLabel(e: EdgeSnapshot): string {
  return `${e.target.table}.${e.target.column}`;
}

/**
 * Version history / diff / rollback (Enterprise v2, E13-5/6 — the uiux
 * spec's "/mappings/$id/history" route, surfaced as a dockable panel next
 * to Properties/Comments rather than a new page, matching how every other
 * schema-mapper surface in this app is composed).
 */
export default function VersionHistoryPanel({ mappingId, role, onRolledBack }: VersionHistoryPanelProps) {
  const { data, isLoading, isError, refetch } = useWidgetData<VersionListResponse>(
    (signal) => api.get(`/api/v1/mappings/${mappingId}/versions`, { signal }),
    [mappingId],
  );
  const versions = useMemo(() => data?.items ?? [], [data]);
  const canEdit = role === "admin" || role === "analyst";

  const [fromId, setFromId] = useState<number | null>(null);
  const [toId, setToId] = useState<number | null>(null);
  const [diff, setDiff] = useState<VersionDiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [rollingBackId, setRollingBackId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Default the comparison to "previous version -> current" once the
  // versions load — the single most useful diff, without forcing the user
  // to pick anything for the common case of "what changed in the latest publish".
  useEffect(() => {
    if (versions.length >= 2 && fromId === null && toId === null) {
      setFromId(versions[1].id);
      setToId(versions[0].id);
    }
  }, [versions, fromId, toId]);

  useEffect(() => {
    if (fromId === null || toId === null || fromId === toId) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    setDiffError(null);
    api
      .get<VersionDiffResponse>(
        `/api/v1/mappings/${mappingId}/versions/diff?from_version_id=${fromId}&to_version_id=${toId}`,
      )
      .then((d) => {
        if (!cancelled) setDiff(d);
      })
      .catch((err) => {
        if (!cancelled) setDiffError(err instanceof ApiError ? err.message : "Failed to load diff.");
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mappingId, fromId, toId]);

  const rollback = async (versionId: number, versionNumber: number) => {
    if (
      !confirm(
        `Roll back to v${versionNumber}? This reopens the mapping for editing from that version's field mappings.`,
      )
    ) {
      return;
    }
    setRollingBackId(versionId);
    setActionError(null);
    try {
      await api.post(`/api/v1/mappings/${mappingId}/versions/${versionId}/rollback`, {});
      await onRolledBack();
      refetch();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Rollback failed.");
    } finally {
      setRollingBackId(null);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {isLoading && <p className="text-fg-subtle italic">Loading versions…</p>}
        {isError && <p className="text-danger">Failed to load version history.</p>}
        {!isLoading && !isError && versions.length === 0 && (
          <p className="text-fg-subtle italic">No published versions yet.</p>
        )}

        {versions.length > 0 && (
          <ul className="space-y-1.5">
            {versions.map((v) => (
              <li
                key={v.id}
                className={classNames(
                  "rounded-md border px-2 py-1.5",
                  v.is_current ? "border-success/30 bg-success/5" : "border-border bg-surface-overlay",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center">
                    <span className="font-semibold text-fg-muted">{`v${v.version_number}`}</span>
                    {v.is_current && (
                      <span className="ml-1.5 rounded bg-success/15 px-1 py-0.5 text-[9px] font-bold uppercase text-success">
                        Current
                      </span>
                    )}
                  </span>
                  {canEdit && !v.is_current && (
                    <button
                      type="button"
                      onClick={() => void rollback(v.id, v.version_number)}
                      disabled={rollingBackId !== null}
                      className="shrink-0 text-[10px] font-semibold rounded bg-warning/10 text-warning border border-warning/30 px-1.5 py-0.5 hover:bg-warning/20 disabled:opacity-50"
                      aria-label={`Roll back to v${v.version_number}`}
                    >
                      {rollingBackId === v.id ? "…" : "Rollback"}
                    </button>
                  )}
                </div>
                <p className="mt-1 text-[10px] text-fg-subtle">
                  {v.edge_count} field{v.edge_count === 1 ? "" : "s"} · published{" "}
                  {formatTimestamp(v.published_at)} by {v.published_by ?? "—"}
                </p>
              </li>
            ))}
          </ul>
        )}
        {actionError && <p className="text-danger">{actionError}</p>}

        {versions.length >= 2 && (
          <div className="border-t border-border pt-3">
            <p className="font-semibold text-fg-muted mb-1.5">Compare versions</p>
            <div className="flex items-center gap-1.5">
              <select
                aria-label="Compare from version"
                value={fromId ?? ""}
                onChange={(e) => setFromId(Number(e.target.value))}
                className="flex-1 rounded border border-border-strong bg-surface-overlay px-1.5 py-1 text-fg"
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {`v${v.version_number}`}
                  </option>
                ))}
              </select>
              <span className="text-fg-subtle" aria-hidden="true">
                →
              </span>
              <select
                aria-label="Compare to version"
                value={toId ?? ""}
                onChange={(e) => setToId(Number(e.target.value))}
                className="flex-1 rounded border border-border-strong bg-surface-overlay px-1.5 py-1 text-fg"
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {`v${v.version_number}`}
                  </option>
                ))}
              </select>
            </div>

            {fromId !== null && toId !== null && fromId === toId && (
              <p className="mt-2 text-fg-subtle italic">Pick two different versions to compare.</p>
            )}
            {diffLoading && <p className="mt-2 text-fg-subtle italic">Loading diff…</p>}
            {diffError && <p className="mt-2 text-danger">{diffError}</p>}
            {diff && (
              <div className="mt-2 space-y-1.5">
                <p className="text-[10px] text-fg-subtle">
                  +{diff.added.length} added · −{diff.removed.length} removed · ~{diff.changed.length}{" "}
                  changed · {diff.unchanged_count} unchanged
                </p>
                {diff.added.length === 0 && diff.removed.length === 0 && diff.changed.length === 0 && (
                  <p className="text-fg-subtle italic">No differences.</p>
                )}
                {diff.added.map((e) => (
                  <div
                    key={`added-${edgeLabel(e)}`}
                    className="rounded border border-success/30 bg-success/5 px-2 py-1"
                  >
                    <span className="font-semibold text-success">+ {edgeLabel(e)}</span>
                  </div>
                ))}
                {diff.removed.map((e) => (
                  <div
                    key={`removed-${edgeLabel(e)}`}
                    className="rounded border border-danger/30 bg-danger/5 px-2 py-1"
                  >
                    <span className="font-semibold text-danger">− {edgeLabel(e)}</span>
                  </div>
                ))}
                {diff.changed.map((c) => (
                  <div
                    key={`changed-${c.target_table}.${c.target_column}`}
                    className="rounded border border-warning/30 bg-warning/5 px-2 py-1"
                  >
                    <span className="font-semibold text-warning">
                      ~ {c.target_table}.{c.target_column}
                    </span>
                    <p className="mt-0.5 text-[10px] text-fg-subtle">
                      {c.before.transformation.kind} → {c.after.transformation.kind}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
