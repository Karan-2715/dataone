"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type {
  CatalogTable, CatalogTableListResponse, ConnectorRef, DriftHistoryResponse,
  ProfileEnqueueResult, Role, ScanResult,
} from "../lib/types";

interface Toast {
  message: string;
  kind: "success" | "error";
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

export function useCatalog() {
  const [role, setRole] = useState<Role | null>(null);

  const [connections, setConnections] = useState<ConnectorRef[]>([]);
  const [connectionsLoading, setConnectionsLoading] = useState(true);
  const [connectionId, setConnectionId] = useState<number | null>(null);

  const [tables, setTables] = useState<CatalogTable[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [dataType, setDataType] = useState("");
  const [classificationLabel, setClassificationLabel] = useState("");

  const [driftHistory, setDriftHistory] = useState<DriftHistoryResponse | null>(null);
  const [scanning, setScanning] = useState(false);
  const [profiling, setProfiling] = useState(false);

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

  useEffect(() => {
    void (async () => {
      setConnectionsLoading(true);
      try {
        const data = await api.get<ConnectorRef[]>("/api/v1/connectors/");
        setConnections(data);
        const params = new URLSearchParams(window.location.search);
        const requestedId = Number(params.get("connection_id"));
        const selected = data.find((connection) => connection.id === requestedId);
        if (data.length > 0) setConnectionId(selected?.id ?? data[0].id);
        const requestedQuery = params.get("q");
        if (requestedQuery) setQ(requestedQuery);
      } catch (err) {
        showError(errorMessage(err, "Failed to load connections."));
      } finally {
        setConnectionsLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchCatalog = useCallback(async () => {
    if (connectionId === null) return;
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (dataType) params.set("data_type", dataType);
      if (classificationLabel) params.set("classification_label", classificationLabel);
      const data = await api.get<CatalogTableListResponse>(
        `/api/v1/catalog/${connectionId}/tables?${params.toString()}`,
      );
      setTables(data.tables);
    } catch (err) {
      setCatalogError(errorMessage(err, "Failed to load catalog."));
      setTables([]);
    } finally {
      setCatalogLoading(false);
    }
  }, [connectionId, q, dataType, classificationLabel]);

  useEffect(() => {
    void fetchCatalog();
  }, [fetchCatalog]);

  const fetchDriftHistory = useCallback(async () => {
    if (connectionId === null) return;
    try {
      const data = await api.get<DriftHistoryResponse>(`/api/v1/schema/${connectionId}/drift-history`);
      setDriftHistory(data);
    } catch {
      setDriftHistory(null);
    }
  }, [connectionId]);

  useEffect(() => {
    void fetchDriftHistory();
  }, [fetchDriftHistory]);

  const scanConnection = useCallback(async () => {
    if (connectionId === null) return;
    setScanning(true);
    try {
      const result = await api.post<ScanResult>(`/api/v1/catalog/scan/${connectionId}`, {});
      showSuccess(`Scanned ${result.tables_scanned} table(s), ${result.columns_scanned} column(s).`);
      await fetchCatalog();
    } catch (err) {
      showError(errorMessage(err, "Scan failed."));
    } finally {
      setScanning(false);
    }
  }, [connectionId, fetchCatalog, showError, showSuccess]);

  const rescanForDrift = useCallback(async () => {
    if (connectionId === null) return;
    try {
      await api.post(`/api/v1/schema/${connectionId}/rescan`, {});
      showSuccess("Drift re-scan complete.");
      await fetchDriftHistory();
    } catch (err) {
      showError(errorMessage(err, "Drift re-scan failed."));
    }
  }, [connectionId, fetchDriftHistory, showError, showSuccess]);

  const profileConnection = useCallback(async () => {
    if (connectionId === null) return;
    setProfiling(true);
    try {
      const result = await api.post<ProfileEnqueueResult>(`/api/v1/catalog/${connectionId}/profile`, {});
      showSuccess(result.message);
    } catch (err) {
      showError(errorMessage(err, "Failed to enqueue profiling."));
    } finally {
      setProfiling(false);
    }
  }, [connectionId, showError, showSuccess]);

  const overrideClassification = useCallback(
    async (columnId: number, label: string, level: string) => {
      try {
        await api.put(`/api/v1/catalog/columns/${columnId}/classification`, { label, level });
        showSuccess("Classification updated.");
        await fetchCatalog();
      } catch (err) {
        showError(errorMessage(err, "Failed to update classification."));
        throw err;
      }
    },
    [fetchCatalog, showError, showSuccess],
  );

  return {
    role,
    connections, connectionsLoading, connectionId, setConnectionId,
    tables, catalogLoading, catalogError, fetchCatalog,
    q, setQ, dataType, setDataType, classificationLabel, setClassificationLabel,
    driftHistory, fetchDriftHistory,
    scanConnection, scanning,
    rescanForDrift,
    profileConnection, profiling,
    overrideClassification,
    toast, showError, showSuccess, clearToast,
  };
}
