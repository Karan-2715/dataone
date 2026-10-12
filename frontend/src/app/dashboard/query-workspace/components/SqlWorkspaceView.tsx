"use client";
import { useCallback, useEffect, useImperativeHandle, forwardRef, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import SqlEditor from "../../query-studio/components/SqlEditor";
import ConnectionSelector from "../../query-studio/components/ConnectionSelector";
import ResultsTable from "../../query-studio/components/ResultsTable";
import HistoryPanel from "../../query-studio/components/HistoryPanel";
import SavedQueriesPanel from "../../query-studio/components/SavedQueriesPanel";
import { writeVisualizeQueryHandoff } from "../../visualize/lib/queryHandoff";
import type {
  CatalogTableListResponse, Connection, HistoryResponse,
  QueryExecuteResult, SavedQuery,
} from "../../query-studio/lib/types";

const DEFAULT_PAGE_SIZE = 100;

export interface SqlWorkspaceViewHandle {
  confirmWrite: () => void;
  cancelWrite: () => void;
}

export interface WriteConfirmDetails {
  statementType: string;
  warnings: string[];
}

interface SqlWorkspaceViewProps {
  connections: Connection[];
  connectionId: number | null;
  setConnectionId: (id: number) => void;
  /** External setter for sqlText — used by in-shell handoff & WorkspaceHandoff */
  externalSqlText?: string;
  onSqlTextApplied?: () => void;
  /** Called when pendingConfirm state changes */
  onPendingConfirmChange?: (pending: boolean) => void;
  /**
   * Called with the details the shell-level WriteConfirmModal needs
   * whenever they change (or null once there's nothing pending) — the
   * shell must not read these off a ref during render (React refs are only
   * safe to read in event handlers/effects, never in the render body).
   */
  onWriteConfirmDetailsChange?: (details: WriteConfirmDetails | null) => void;
  /** Called when running state transitions from true to false (background completion) */
  onBackgroundComplete?: () => void;
}

const SqlWorkspaceView = forwardRef<SqlWorkspaceViewHandle, SqlWorkspaceViewProps>(function SqlWorkspaceView({
  connections,
  connectionId,
  setConnectionId,
  externalSqlText,
  onSqlTextApplied,
  onPendingConfirmChange,
  onWriteConfirmDetailsChange,
  onBackgroundComplete,
}, ref) {
  const router = useRouter();
  const [sqlText, setSqlText] = useState("");
  const [catalogTables, setCatalogTables] = useState<CatalogTableListResponse["tables"]>([]);
  const [result, setResult] = useState<QueryExecuteResult | null>(null);
  const [page, setPage] = useState(1);
  const [running, setRunning] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"saved" | "history">("history");
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // Apply externalSqlText when it changes (from handoff)
  useEffect(() => {
    if (externalSqlText !== undefined && externalSqlText !== null) {
      setSqlText(externalSqlText);
      onSqlTextApplied?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalSqlText]);

  // Notify parent of pendingConfirm changes
  useEffect(() => {
    onPendingConfirmChange?.(pendingConfirm);
  }, [pendingConfirm, onPendingConfirmChange]);

  // Report the shell-level modal's data reactively — never let the parent
  // read it off a ref during render (react-hooks/refs: refs are only safe
  // to read in event handlers/effects, not in the render body).
  useEffect(() => {
    onWriteConfirmDetailsChange?.(
      pendingConfirm ? { statementType: result?.statement_type ?? "write", warnings: result?.warnings ?? [] } : null
    );
  }, [pendingConfirm, result, onWriteConfirmDetailsChange]);

  // Track running → false transitions for background-completion badge
  const prevRunningRef = useRef(running);
  useEffect(() => {
    if (prevRunningRef.current && !running) {
      onBackgroundComplete?.();
    }
    prevRunningRef.current = running;
  }, [running, onBackgroundComplete]);

  // Fetch catalog tables and saved queries when connection changes
  useEffect(() => {
    if (connectionId == null) return;
    api.get<CatalogTableListResponse>(`/api/v1/catalog/${connectionId}/tables`)
      .then((data) => setCatalogTables(data.tables))
      .catch(() => setCatalogTables([]));
    api.get<SavedQuery[]>(`/api/v1/query-studio/saved?connection_id=${connectionId}`)
      .then(setSavedQueries)
      .catch(() => setSavedQueries([]));
  }, [connectionId]);

  const refreshHistory = useCallback(() => {
    api.get<HistoryResponse>("/api/v1/query-studio/history")
      .then(setHistory)
      .catch(() => setHistory(null));
  }, []);

  useEffect(() => { refreshHistory(); }, [refreshHistory]);

  const runQuery = useCallback(async (opts: { confirm?: boolean; page?: number } = {}) => {
    if (connectionId == null || !sqlText.trim()) return;
    setRunning(true);
    setErrorBanner(null);
    const targetPage = opts.page ?? page;
    try {
      const data = await api.post<QueryExecuteResult>("/api/v1/query-studio/execute", {
        connection_id: connectionId,
        sql: sqlText,
        page: targetPage,
        page_size: DEFAULT_PAGE_SIZE,
        confirm: opts.confirm ?? false,
      });
      setResult(data);
      setPage(targetPage);
      if (data.requires_confirmation) {
        setPendingConfirm(true);
      } else {
        setPendingConfirm(false);
        refreshHistory();
      }
    } catch (err) {
      setErrorBanner(err instanceof ApiError ? err.message : "Query execution failed — is the API reachable?");
    } finally {
      setRunning(false);
    }
  }, [connectionId, sqlText, page, refreshHistory]);

  const changePage = (newPage: number) => runQuery({ page: newPage });

  const exportCsv = async () => {
    if (connectionId == null || !sqlText.trim()) return;
    try {
      const { blob, filename } = await api.downloadPost("/api/v1/query-studio/export", {
        connection_id: connectionId, sql: sqlText, page: 1, page_size: DEFAULT_PAGE_SIZE,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setErrorBanner(err instanceof ApiError ? err.message : "Export failed");
    }
  };

  const saveCurrentQuery = async () => {
    if (connectionId == null || !sqlText.trim()) return;
    const name = window.prompt("Name this query:");
    if (!name) return;
    try {
      await api.post("/api/v1/query-studio/saved", { connection_id: connectionId, name, sql_text: sqlText });
      const updated = await api.get<SavedQuery[]>(`/api/v1/query-studio/saved?connection_id=${connectionId}`);
      setSavedQueries(updated);
    } catch (err) {
      setErrorBanner(err instanceof ApiError ? err.message : "Could not save query");
    }
  };

  const deleteSavedQuery = async (id: number) => {
    try {
      await api.delete(`/api/v1/query-studio/saved/${id}`);
      setSavedQueries((qs) => qs.filter((q) => q.id !== id));
    } catch (err) {
      setErrorBanner(err instanceof ApiError ? err.message : "Could not delete query");
    }
  };

  const loadSavedQuery = (q: SavedQuery) => {
    setConnectionId(q.connection_id);
    setSqlText(q.sql_text);
    setResult(null);
  };

  const visualizeResult = () => {
    if (connectionId == null || !result?.executed || result.rows.length === 0) return;
    writeVisualizeQueryHandoff({
      connectionId,
      sql: sqlText,
      columns: result.columns,
      rows: result.rows,
    });
    router.push("/dashboard/visualize?source=query");
  };

  const connectionType = connections.find((c) => c.id === connectionId)?.type;

  // Expose imperative handle to parent — event-handler-only calls
  // (confirm/cancel), safe under react-hooks/refs since they're never
  // invoked during render.
  useImperativeHandle(ref, () => ({
    confirmWrite: () => runQuery({ confirm: true, page: 1 }),
    cancelWrite: () => setPendingConfirm(false),
  }), [runQuery]);

  return (
    <div className="flex h-full flex-col xl:flex-row">
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex flex-col gap-3 border-b border-border bg-glass-bg p-4 backdrop-blur-sm">
          <div className="flex justify-between items-center">
            <div>
              <h2 className="text-sm font-semibold text-fg">SQL Workspace</h2>
              <p className="text-xs text-fg-subtle">Run SQL against the selected connection, then inspect or export the results.</p>
            </div>
            <ConnectionSelector connections={connections} value={connectionId} onChange={setConnectionId} />
          </div>

          <SqlEditor
            value={sqlText}
            onChange={setSqlText}
            connectionType={connectionType}
            tables={catalogTables}
            onRun={() => runQuery({ page: 1 })}
          />

          <div className="flex items-center gap-2">
            <button
              onClick={() => runQuery({ page: 1 })}
              disabled={running || !sqlText.trim() || connectionId == null}
              className="workspace-primary-action px-5 py-2"
            >
              {running ? "Running…" : "▶ Run (⌘/Ctrl+Enter)"}
            </button>
            <button
              onClick={exportCsv}
              disabled={!sqlText.trim() || connectionId == null}
              className="rounded-lg border border-border bg-surface-overlay px-3 py-2 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
            >
              Export CSV
            </button>
            <button
              onClick={saveCurrentQuery}
              disabled={!sqlText.trim() || connectionId == null}
              className="rounded-lg border border-border bg-surface-overlay px-3 py-2 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
            >
              Save
            </button>
            <button
              onClick={visualizeResult}
              disabled={!result?.executed || result.rows.length === 0 || connectionId == null}
              className="rounded-lg border border-border bg-surface-overlay px-3 py-2 text-xs font-semibold text-fg-muted hover:border-accent/30 hover:text-accent disabled:opacity-50"
            >
              Visualize this result
            </button>
          </div>

          {errorBanner && (
            <div className="rounded-lg border border-danger/20 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">
              {errorBanner}
            </div>
          )}
          {result && result.warnings.length > 0 && !result.requires_confirmation && (
            <div className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs text-warning">
              {result.warnings.join(" ")}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {result ? (
            <ResultsTable result={result} page={page} onPageChange={changePage} />
          ) : (
            <div className="flex h-full flex-1 flex-col items-center justify-center gap-3 pt-16 text-fg-subtle">
              <span className="text-4xl" aria-hidden="true">⌁</span>
              <span className="text-sm">Write a query and run it to see results here.</span>
            </div>
          )}
        </div>
      </div>

      <aside className="flex max-h-56 w-full flex-col border-t border-border bg-glass-bg xl:max-h-none xl:w-72 xl:border-l xl:border-t-0" aria-label="Query resources">
        <div className="workspace-segments m-2 grid grid-cols-2" role="tablist" aria-label="Query resources">
          <button
            onClick={() => setSidebarTab("history")}
            role="tab"
            aria-selected={sidebarTab === "history"}
            className={sidebarTab === "history" ? "workspace-segment workspace-segment-active" : "workspace-segment"}
          >
            History
          </button>
          <button
            onClick={() => setSidebarTab("saved")}
            role="tab"
            aria-selected={sidebarTab === "saved"}
            className={sidebarTab === "saved" ? "workspace-segment workspace-segment-active" : "workspace-segment"}
          >
            Saved
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {sidebarTab === "history" ? (
            <HistoryPanel entries={history?.history ?? []} onLoad={setSqlText} />
          ) : (
            <SavedQueriesPanel queries={savedQueries} onLoad={loadSavedQuery} onDelete={deleteSavedQuery} />
          )}
        </div>
      </aside>
    </div>
  );
});

export default SqlWorkspaceView;
