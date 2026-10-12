"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Connection } from "../query-studio/lib/types";
import WriteConfirmModal from "../query-studio/components/WriteConfirmModal";
import GenieChatView from "./components/GenieChatView";
import SqlWorkspaceView, { type SqlWorkspaceViewHandle, type WriteConfirmDetails } from "./components/SqlWorkspaceView";
import { readAndClearWorkspaceHandoff, type WorkspaceHandoff } from "./lib/handoff";
import { SegmentedControl, WorkspaceHeader } from "../components";

const MODE_DISPLAY_NAMES: Record<string, string> = {
  schema_intel: "Schema Intel",
  schema_mapper: "Schema Mapper",
};

function QueryWorkspaceInner() {
  const searchParams = useSearchParams();

  // ── WorkspaceHandoff from Schema Intel / Schema Mapper (task #9) ─
  // Read (and clear) exactly once via a lazy useState initializer rather
  // than an effect — an effect that calls setState only on mount (empty
  // deps) causes an extra render pass for no benefit; a lazy initializer
  // resolves everything it seeds in the very first render. Must be declared
  // before mode/connectionId/etc. below since they read from it.
  const [handoff] = useState<WorkspaceHandoff | null>(() =>
    typeof window === "undefined" ? null : readAndClearWorkspaceHandoff()
  );

  // ── Mode state — handoff's mode wins over ?mode= (decision #10) ─
  const [mode, setMode] = useState<"ask" | "sql">(() => {
    if (handoff) return handoff.mode;
    const m = searchParams.get("mode");
    if (m === "sql" || m === "ask") return m;
    return "ask";
  });

  // ── Shared connection state (single fetch, task #2) ────────────
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionId, setConnectionId] = useState<number | null>(() => handoff?.connectionId ?? null);
  const [connectionsError, setConnectionsError] = useState<string | null>(null);
  const connectionsFetchedRef = useRef(false);

  const loadConnections = useCallback(async () => {
    setConnectionsError(null);
    try {
      const data = await api.get<Connection[]>("/api/v1/connectors/");
      setConnections(data);
      if (data.length > 0) setConnectionId((cur) => cur ?? data[0].id);
    } catch (err) {
      setConnections([]);
      setConnectionsError(err instanceof Error ? err.message : "Unable to load connections.");
    }
  }, []);

  useEffect(() => {
    if (connectionsFetchedRef.current) return;
    connectionsFetchedRef.current = true;
    const timer = window.setTimeout(() => void loadConnections(), 0);
    return () => window.clearTimeout(timer);
  }, [loadConnections]);

  // ── Ref to SqlWorkspaceView for imperative access (write-confirm modal only) ──
  const sqlViewRef = useRef<SqlWorkspaceViewHandle>(null);

  // ── SQL text handed off from AskData or from a WorkspaceHandoff ─
  // Applied declaratively via SqlWorkspaceView's externalSqlText prop —
  // both subviews are always mounted (decision #1), so there's no need
  // to defer through a ref + setTimeout.
  const [externalSqlText, setExternalSqlText] = useState<string | undefined>(() =>
    handoff?.mode === "sql" ? handoff.sql : undefined
  );
  const clearExternalSqlText = useCallback(() => setExternalSqlText(undefined), []);

  // ── In-shell handoff from AskData → SQL mode (task #3) ─────────
  const handleEditInSql = useCallback((handoffConnectionId: number, sql: string) => {
    setConnectionId(handoffConnectionId);
    setMode("sql");
    setExternalSqlText(sql);
  }, []);

  const [handoffBanner, setHandoffBanner] = useState<WorkspaceHandoff["banner"] | null>(
    () => handoff?.banner ?? null
  );
  const [prefillQuestion] = useState<string | undefined>(() =>
    handoff?.mode === "ask" ? handoff.prefillQuestion : undefined
  );

  // ── Mode-switch guardrails (task #4) ──────────────────────────
  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [writeConfirmDetails, setWriteConfirmDetails] = useState<WriteConfirmDetails | null>(null);
  const [bgAskComplete, setBgAskComplete] = useState(false);
  const [bgSqlComplete, setBgSqlComplete] = useState(false);

  const handlePendingConfirmChange = useCallback((pending: boolean) => {
    setPendingConfirm(pending);
  }, []);

  const handleSqlBackgroundComplete = useCallback(() => {
    if (mode !== "sql") {
      setBgSqlComplete(true);
      setTimeout(() => setBgSqlComplete(false), 3000);
    }
  }, [mode]);

  const handleAskBackgroundComplete = useCallback(() => {
    if (mode !== "ask") {
      setBgAskComplete(true);
      setTimeout(() => setBgAskComplete(false), 3000);
    }
  }, [mode]);

  // ── Mode toggle handler ────────────────────────────────────────
  const handleModeChange = useCallback((newMode: "ask" | "sql") => {
    if (newMode === mode) return;
    setMode(newMode);
    setBgAskComplete(false);
    setBgSqlComplete(false);
  }, [mode]);

  return (
    <div className="workspace-page flex h-full flex-col">
      {/* ── Header with mode toggle ─────────────────────────────── */}
      <WorkspaceHeader
        title="Query Workspace"
        description="Ask Genie in plain English about the Databricks data you pick, or work directly in SQL. Your connection and in-progress work stay intact when you switch modes."
        className="shrink-0 border-b border-border bg-background px-4 py-4 md:px-6 z-10"
        actions={
          <SegmentedControl
            label="Query workspace mode"
            value={mode}
            onChange={handleModeChange}
            options={[
              { value: "ask", label: "Genie (Databricks)", indicator: bgAskComplete },
              { value: "sql", label: "SQL Editor", indicator: bgSqlComplete },
            ]}
          />
        }
      />

      {/* ── WorkspaceHandoff banner (task #9) ───────────────────── */}
      {handoffBanner && (
        <div className="flex items-center justify-between border-b border-accent/20 bg-accent-soft px-4 py-2">
          <span className="text-xs text-accent">
             Investigating: {handoffBanner.summary} — from{" "}
            {MODE_DISPLAY_NAMES[handoffBanner.sourceModule] ?? handoffBanner.sourceModule}
          </span>
          <button
            onClick={() => setHandoffBanner(null)}
            className="ml-2 text-xs font-semibold text-accent hover:opacity-80"
          >
            Dismiss
          </button>
        </div>
      )}

      {connectionsError && (
        <div className="flex items-center justify-between gap-3 border-b border-danger/20 bg-danger/10 px-4 py-2">
          <span className="text-xs text-danger">{connectionsError}</span>
          <button
            type="button"
            onClick={() => void loadConnections()}
            className="shrink-0 rounded-lg border border-danger/30 px-2 py-1 text-xs font-semibold text-danger hover:bg-danger/10"
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Both subviews kept mounted, toggled with `hidden` class (decision #1) ── */}
      <div className={`flex-1 overflow-hidden ${mode === "ask" ? "" : "hidden"}`}>
        <GenieChatView
          connections={connections}
          connectionId={connectionId}
          prefillQuestion={prefillQuestion}
          onEditInSql={handleEditInSql}
          onBackgroundComplete={handleAskBackgroundComplete}
        />
      </div>
      <div className={`flex-1 overflow-hidden ${mode === "sql" ? "" : "hidden"}`}>
        <SqlWorkspaceView
          ref={sqlViewRef}
          connections={connections}
          connectionId={connectionId}
          setConnectionId={setConnectionId}
          externalSqlText={externalSqlText}
          onSqlTextApplied={clearExternalSqlText}
          onPendingConfirmChange={handlePendingConfirmChange}
          onWriteConfirmDetailsChange={setWriteConfirmDetails}
          onBackgroundComplete={handleSqlBackgroundComplete}
        />
      </div>

      {/* ── Write-confirm modal rendered at shell level (task #4) ──
          Data comes from writeConfirmDetails state (reported reactively by
          SqlWorkspaceView), never read off sqlViewRef during render. */}
      {pendingConfirm && writeConfirmDetails && (
        <WriteConfirmModal
          statementType={writeConfirmDetails.statementType}
          warnings={writeConfirmDetails.warnings}
          onConfirm={() => {
            sqlViewRef.current?.confirmWrite();
            setPendingConfirm(false);
          }}
          onCancel={() => {
            sqlViewRef.current?.cancelWrite();
            setPendingConfirm(false);
          }}
        />
      )}
    </div>
  );
}

export default QueryWorkspaceInner;
