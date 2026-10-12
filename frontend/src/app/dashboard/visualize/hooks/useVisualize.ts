"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type {
  CatalogTableRef, ChartType, ConnectorRef, FilterSpec, MeasureSpec,
  Role, VizQueryResponse, VizView, VizViewListResponse,
} from "../lib/types";
import { aggregateQueryRows } from "../lib/clientAggregate";
import { readAndClearVisualizeQueryHandoff, type VisualizeQueryHandoff } from "../lib/queryHandoff";

const QUERY_RESULT_TABLE_NAME = "Query Workspace result";

interface Toast {
  message: string;
  kind: "success" | "error";
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

export function useVisualize() {
  const [role, setRole] = useState<Role | null>(null);

  const [connections, setConnections] = useState<ConnectorRef[]>([]);
  const [connectionId, setConnectionId] = useState<number | null>(null);

  const [catalogTables, setCatalogTables] = useState<CatalogTableRef[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [tableName, setTableName] = useState<string | null>(null);

  const [dataSourceMode, setDataSourceMode] = useState<"catalog" | "query">("catalog");
  const [queryHandoff, setQueryHandoff] = useState<VisualizeQueryHandoff | null>(null);
  // Synchronous flag (not state) so the connections-fetch effect below can
  // tell, within the same mount pass, whether the handoff already picked a
  // connection — avoids a re-render race with async state updates.
  const handoffAppliedRef = useRef(false);

  const [chartType, setChartType] = useState<ChartType>("bar");
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [measures, setMeasures] = useState<MeasureSpec[]>([]);
  const [filters, setFilters] = useState<FilterSpec[]>([]);

  const [result, setResult] = useState<VizQueryResponse | null>(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  const [savedViews, setSavedViews] = useState<VizView[]>([]);

  const [toast, setToast] = useState<Toast | null>(null);
  const showError = useCallback((message: string) => setToast({ message, kind: "error" }), []);
  const showSuccess = useCallback((message: string) => setToast({ message, kind: "success" }), []);
  const clearToast = useCallback(() => setToast(null), []);

  useEffect(() => {
    void (async () => {
      try {
        const me = await api.get<{ role: Role }>("/api/v1/auth/me");
        setRole(me.role);
      } catch {
        setRole(null);
      }
    })();
  }, []);

  // Consume a Query Workspace handoff (if navigated here via
  // /dashboard/visualize?source=query) before the connections/catalog
  // fetches below get a chance to pick a default connection/table.
  useEffect(() => {
    const handoff = readAndClearVisualizeQueryHandoff();
    if (!handoff) return;
    handoffAppliedRef.current = true;
    setDataSourceMode("query");
    setQueryHandoff(handoff);
    setConnectionId(handoff.connectionId);
    setCatalogTables([{
      id: 0,
      table_name: QUERY_RESULT_TABLE_NAME,
      columns: handoff.columns.map((name, i) => ({ id: i, column_name: name, data_type: null })),
    }]);
    setTableName(QUERY_RESULT_TABLE_NAME);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<ConnectorRef[]>("/api/v1/connectors/");
        setConnections(data);
        if (data.length > 0 && !handoffAppliedRef.current) setConnectionId(data[0].id);
      } catch (err) {
        showError(errorMessage(err, "Failed to load connections."));
      }
    })();
  }, [showError]);

  const fetchCatalog = useCallback(async () => {
    if (dataSourceMode === "query") return;
    if (connectionId === null) return;
    setCatalogLoading(true);
    setTableName(null);
    setDimensions([]);
    setMeasures([]);
    setResult(null);
    try {
      const data = await api.get<{ tables: CatalogTableRef[] }>(`/api/v1/catalog/${connectionId}/tables`);
      setCatalogTables(data.tables);
      if (data.tables.length > 0) setTableName(data.tables[0].table_name);
    } catch (err) {
      showError(errorMessage(err, "Failed to load catalog."));
      setCatalogTables([]);
    } finally {
      setCatalogLoading(false);
    }
  }, [connectionId, dataSourceMode, showError]);

  useEffect(() => {
    void fetchCatalog();
  }, [fetchCatalog]);

  const fetchSavedViews = useCallback(async () => {
    try {
      const data = await api.get<VizViewListResponse>("/api/v1/viz/views");
      setSavedViews(data.items);
    } catch (err) {
      showError(errorMessage(err, "Failed to load saved views."));
    }
  }, [showError]);

  useEffect(() => {
    void fetchSavedViews();
  }, [fetchSavedViews]);

  const runQuery = useCallback(async () => {
    if (dataSourceMode === "query") {
      if (!queryHandoff) return;
      if (dimensions.length === 0 && measures.length === 0) {
        setResult(null);
        return;
      }
      setQueryError(null);
      setResult(aggregateQueryRows(queryHandoff.rows, dimensions, measures, filters));
      return;
    }
    if (connectionId === null || tableName === null) return;
    if (dimensions.length === 0 && measures.length === 0) {
      setResult(null);
      return;
    }
    setQueryLoading(true);
    setQueryError(null);
    try {
      const data = await api.post<VizQueryResponse>("/api/v1/viz/query", {
        connection_id: connectionId, table_name: tableName,
        dimensions, measures, filters,
      });
      setResult(data);
    } catch (err) {
      setQueryError(errorMessage(err, "Query failed."));
      setResult(null);
    } finally {
      setQueryLoading(false);
    }
  }, [dataSourceMode, queryHandoff, connectionId, tableName, dimensions, measures, filters]);

  // Debounced auto-query on config change (300ms, per spec's "rapid filter
  // changes" edge case).
  useEffect(() => {
    const timer = setTimeout(() => void runQuery(), 300);
    return () => clearTimeout(timer);
  }, [runQuery]);

  const resetToCatalogMode = useCallback(() => {
    setQueryHandoff(null);
    setDataSourceMode("catalog");
  }, []);

  const saveView = useCallback(
    async (name: string) => {
      // Saved views are catalog-table-only (VizView has no column to store
      // raw SQL/rows) — the UI hides the Save action in query mode too.
      if (dataSourceMode === "query") {
        showError("Saved views aren't supported for Query Workspace results yet.");
        return;
      }
      if (connectionId === null || tableName === null) return;
      try {
        const view = await api.post<VizView>("/api/v1/viz/views", {
          name, connection_id: connectionId, table_name: tableName,
          chart_type: chartType, dimensions, measures, filters,
        });
        setSavedViews((prev) => [view, ...prev]);
        showSuccess(`View "${name}" saved.`);
      } catch (err) {
        showError(errorMessage(err, "Failed to save view."));
        throw err;
      }
    },
    [dataSourceMode, connectionId, tableName, chartType, dimensions, measures, filters, showError, showSuccess],
  );

  const loadView = useCallback(
    (view: VizView) => {
      setConnectionId(view.connection_id);
      setTableName(view.table_name);
      setChartType(view.chart_type);
      setDimensions(view.dimensions);
      setMeasures(view.measures);
      setFilters(view.filters);
    },
    [],
  );

  const deleteView = useCallback(
    async (viewId: number) => {
      try {
        await api.delete(`/api/v1/viz/views/${viewId}`);
        setSavedViews((prev) => prev.filter((v) => v.id !== viewId));
        showSuccess("View deleted.");
      } catch (err) {
        showError(errorMessage(err, "Failed to delete view."));
      }
    },
    [showError, showSuccess],
  );

  return {
    role,
    connections, connectionId, setConnectionId,
    catalogTables, catalogLoading, tableName, setTableName,
    chartType, setChartType,
    dimensions, setDimensions,
    measures, setMeasures,
    filters, setFilters,
    result, queryLoading, queryError, runQuery,
    savedViews, saveView, loadView, deleteView,
    toast, showError, showSuccess, clearToast,
    dataSourceMode, queryHandoff, resetToCatalogMode,
  };
}
