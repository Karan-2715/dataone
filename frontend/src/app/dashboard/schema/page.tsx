"use client";
/**
 * Schema Intel — catalog browsing, profiling, classification, and drift
 * (schema_intel_tasks Task #5).
 *
 * Replaces the pre-TRD "Schema Matcher" (AI-based table/column matching
 * against POST /api/v1/agent/schema-match). That functionality is
 * duplicated by Schema Mapper's own AI-suggestion flow and Pipelines'
 * legacy AI matcher — nothing else in the frontend links to this route's
 * matcher UI, so it's a clean replacement, not a fork.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useCatalog } from "./hooks/useCatalog";

import ConnectionPicker from "./components/ConnectionPicker";
import CatalogSearchBar from "./components/CatalogSearchBar";
import CatalogTableCard from "./components/CatalogTableCard";
import DriftHistoryPanel from "./components/DriftHistoryPanel";
import Toast from "./components/Toast";
import { EmptyState, ErrorState, LoadingState, WorkspaceHeader } from "../components";

export default function SchemaIntelPage() {
  const router = useRouter();
  const c = useCatalog();

  useEffect(() => {
    if (c.catalogError && c.catalogError.toLowerCase().includes("not authenticated")) {
      router.push("/signin");
    }
  }, [c.catalogError, router]);

  const canManage = c.role === "admin" || c.role === "analyst";

  return (
    <div className="workspace-page flex h-full flex-col">
      <WorkspaceHeader
        eyebrow="Schema Intelligence"
        title="Schema Intel"
        description="Browse discovered structures, profile columns, review classifications, and track schema drift."
        className="shrink-0 border-b border-border bg-glass-bg-strong px-4 py-4 backdrop-blur-xl md:px-6"
      />

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <div className="glass flex flex-wrap items-center justify-between gap-3 rounded-xl p-3">
          <div className="flex flex-wrap items-center gap-2">
            <ConnectionPicker
              connections={c.connections}
              loading={c.connectionsLoading}
              connectionId={c.connectionId}
              onChange={c.setConnectionId}
            />
            {canManage && (
              <>
                <button
                  type="button"
                  onClick={() => void c.scanConnection()}
                  disabled={c.scanning || c.connectionId === null}
                  className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-2 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
                >
                  {c.scanning ? "Scanning…" : "Scan Schema"}
                </button>
                <button
                  type="button"
                  onClick={() => void c.profileConnection()}
                  disabled={c.profiling || c.connectionId === null || c.tables.length === 0}
                  title={c.tables.length === 0 ? "Scan the catalog first" : undefined}
                  className="workspace-primary-action min-h-0 rounded-lg px-3 py-2 text-xs"
                >
                  {c.profiling ? "Enqueuing…" : "Profile columns"}
                </button>
              </>
            )}
          </div>
        </div>

        <CatalogSearchBar
          q={c.q}
          onQChange={c.setQ}
          dataType={c.dataType}
          onDataTypeChange={c.setDataType}
          classificationLabel={c.classificationLabel}
          onClassificationLabelChange={c.setClassificationLabel}
        />

        {c.catalogLoading ? (
          <LoadingState label="Loading catalog…" />
        ) : c.catalogError ? (
          <ErrorState title="Catalog could not be loaded" message={c.catalogError} />
        ) : c.tables.length === 0 ? (
          <EmptyState
            icon="🗂️"
            title="No catalog yet"
            description={canManage ? "Scan the selected connection to discover its tables and columns." : "This connection has not been scanned yet."}
            action={canManage ? <button type="button" onClick={() => void c.scanConnection()} disabled={c.scanning || c.connectionId === null} className="workspace-primary-action">{c.scanning ? "Scanning…" : "Scan Schema"}</button> : undefined}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {c.tables.map((t) => (
              <CatalogTableCard key={t.id} table={t} role={c.role} onOverride={c.overrideClassification} />
            ))}
          </div>
        )}

        <DriftHistoryPanel history={c.driftHistory} onRescan={() => void c.rescanForDrift()} role={c.role} connectionId={c.connectionId} />
      </div>

      <Toast toast={c.toast} onDismiss={c.clearToast} />
    </div>
  );
}
