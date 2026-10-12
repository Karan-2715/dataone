"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, EmptyState, ErrorState, Gauge, LoadingState, WorkspaceHeader } from "../components";
import { useWidgetData } from "../hooks/useWidgetData";

const CLASSIFICATION_LABELS = ["PII", "Sensitive", "Confidential", "Public"] as const;
const COMPLIANCE_STATUSES = ["not_assessed", "under_review", "compliant", "non_compliant"] as const;

interface ConnectorRef {
  id: number;
  name: string;
}

interface CatalogTable {
  table_name: string;
}

interface GovernanceRecord {
  id: number;
  table_name: string | null;
  column_name: string | null;
  owner: string | null;
  steward: string | null;
  classification: string | null;
  retention_policy: string | null;
  compliance_status: string;
  updated_by: string;
  updated_at: string;
}

interface GovernanceScore {
  score: number;
  table_count: number;
  owner_coverage: number;
  classification_coverage: number;
  retention_coverage: number;
}

function complianceVariant(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "compliant") return "success";
  if (status === "under_review") return "warning";
  if (status === "non_compliant") return "danger";
  return "neutral";
}

export default function GovernanceCenterPage() {
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [editingTable, setEditingTable] = useState<string | null>(null);
  const [form, setForm] = useState<{
    owner: string; steward: string; classification: string; retention_policy: string; compliance_status: string;
  }>({ owner: "", steward: "", classification: "", retention_policy: "", compliance_status: "not_assessed" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const { data: score } = useWidgetData<GovernanceScore>(
    (signal) => api.get<GovernanceScore>(
      `/api/v1/governance/score${connectionId == null ? "" : `?connection_id=${connectionId}`}`,
      { signal },
    ),
    [connectionId, refreshKey],
  );

  const { data: connections } = useWidgetData<ConnectorRef[]>(
    (signal) => api.get<ConnectorRef[]>("/api/v1/connectors/", { signal }),
    [],
  );

  const { data: catalog } = useWidgetData<{ tables: CatalogTable[] } | null>(
    (signal) => {
      if (connectionId == null) return Promise.resolve(null);
      return api.get<{ tables: CatalogTable[] }>(`/api/v1/catalog/${connectionId}/tables`, { signal });
    },
    [connectionId],
  );

  const { data: records, isLoading, isError, errorMessage } = useWidgetData<GovernanceRecord[]>(
    (signal) => {
      if (connectionId == null) return Promise.resolve([]);
      return api.get<GovernanceRecord[]>(`/api/v1/governance/${connectionId}`, { signal });
    },
    [connectionId, refreshKey],
  );

  const recordByTable = new Map((records ?? []).filter((r) => r.column_name == null).map((r) => [r.table_name, r]));

  const startEdit = (tableName: string) => {
    const existing = recordByTable.get(tableName);
    setForm({
      owner: existing?.owner ?? "",
      steward: existing?.steward ?? "",
      classification: existing?.classification ?? "",
      retention_policy: existing?.retention_policy ?? "",
      compliance_status: existing?.compliance_status ?? "not_assessed",
    });
    setSaveError(null);
    setEditingTable(tableName);
  };

  const save = async () => {
    if (connectionId == null || editingTable == null) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.put(`/api/v1/governance/${connectionId}`, {
        table_name: editingTable,
        owner: form.owner || null,
        steward: form.steward || null,
        classification: form.classification || null,
        retention_policy: form.retention_policy || null,
        compliance_status: form.compliance_status,
      });
      setEditingTable(null);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save governance metadata.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="workspace-page flex h-full flex-col">
      <WorkspaceHeader
        eyebrow="Governance & Compliance"
        title="Governance Intelligence"
        description="Manage owners, stewards, classifications, retention, and compliance status per cataloged asset."
        className="shrink-0 border-b border-border bg-glass-bg-strong px-4 py-4 backdrop-blur-xl md:px-6"
        actions={score ? (
          <div className="flex items-center gap-3">
            <Gauge value={score.score} size="md" />
            <div className="text-xs text-fg-subtle">
              <div>{connectionId == null ? "Platform" : "Selected connection"}</div>
              <div>Owner {score.owner_coverage}%</div>
              <div>Classification {score.classification_coverage}%</div>
              <div>Retention {score.retention_coverage}%</div>
            </div>
          </div>
        ) : undefined}
      />

      <div className="m-3 flex rounded-xl border border-glass-border bg-glass-bg p-3 shadow-[var(--glass-shadow)] backdrop-blur-xl">
        <label className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-fg-subtle">
          Connection
          <select
            value={connectionId ?? ""}
            onChange={(e) => { setConnectionId(e.target.value === "" ? null : Number(e.target.value)); setEditingTable(null); }}
            className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-xs font-semibold text-fg focus:border-accent focus:outline-none"
          >
            <option value="">Select…</option>
            {(connections ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden xl:flex-row">
        <div className="flex-1 overflow-y-auto p-3">
          {connectionId == null ? (
            <EmptyState title="Pick a connection" description="Select a connection above to view and edit its governance metadata." />
          ) : isLoading ? (
            <LoadingState label="Loading governance metadata…" />
          ) : isError ? (
            <ErrorState message={errorMessage} />
          ) : (catalog?.tables ?? []).length === 0 ? (
            <EmptyState title="No cataloged tables" description="Scan this connection from Schema Intel first." />
          ) : (
            <div className="overflow-x-auto"><table className="w-full min-w-[52rem] text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
                  <th className="py-1.5">Table</th>
                  <th>Owner</th>
                  <th>Steward</th>
                  <th>Classification</th>
                  <th>Retention</th>
                  <th>Compliance</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(catalog?.tables ?? []).map((t) => {
                  const record = recordByTable.get(t.table_name);
                  return (
                    <tr key={t.table_name} className="border-t border-border/60">
                      <td className="py-2 font-mono text-fg-muted">{t.table_name}</td>
                      <td className="text-fg-muted">{record?.owner ?? "—"}</td>
                      <td className="text-fg-muted">{record?.steward ?? "—"}</td>
                      <td>{record?.classification ? <Badge variant="info" size="sm">{record.classification}</Badge> : "—"}</td>
                      <td className="text-fg-muted">{record?.retention_policy ?? "—"}</td>
                      <td><Badge variant={complianceVariant(record?.compliance_status ?? "not_assessed")} size="sm">{(record?.compliance_status ?? "not_assessed").replace("_", " ")}</Badge></td>
                      <td>
                        <button type="button" onClick={() => startEdit(t.table_name)} className="rounded-lg px-2 py-1 text-[10px] font-semibold text-accent hover:bg-accent-soft">
                          Edit
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table></div>
          )}
        </div>

        {editingTable && (
          <aside className="max-h-[45%] w-full overflow-y-auto border-t border-border bg-glass-bg p-4 xl:max-h-none xl:w-80 xl:border-l xl:border-t-0">
            <h4 className="text-sm font-semibold text-fg mb-3">Edit: {editingTable}</h4>
            <div className="flex flex-col gap-3 text-xs">
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-fg-subtle">Owner</span>
                <input value={form.owner} onChange={(e) => setForm((f) => ({ ...f, owner: e.target.value }))} className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-fg" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-fg-subtle">Steward</span>
                <input value={form.steward} onChange={(e) => setForm((f) => ({ ...f, steward: e.target.value }))} className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-fg" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-fg-subtle">Classification</span>
                <select value={form.classification} onChange={(e) => setForm((f) => ({ ...f, classification: e.target.value }))} className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-fg">
                  <option value="">Unclassified</option>
                  {CLASSIFICATION_LABELS.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-fg-subtle">Retention policy</span>
                <input value={form.retention_policy} onChange={(e) => setForm((f) => ({ ...f, retention_policy: e.target.value }))} placeholder="e.g. 3 years after inactivity" className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-fg" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-fg-subtle">Compliance status</span>
                <select value={form.compliance_status} onChange={(e) => setForm((f) => ({ ...f, compliance_status: e.target.value }))} className="rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-fg">
                  {COMPLIANCE_STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                </select>
              </label>
              {saveError && <p className="text-danger" role="alert">{saveError}</p>}
              <div className="flex gap-2 mt-2">
                <button type="button" onClick={save} disabled={saving} className="workspace-primary-action min-h-0 flex-1 rounded-lg px-3 py-1.5 text-xs">
                  {saving ? "Saving…" : "Save"}
                </button>
                <button type="button" onClick={() => setEditingTable(null)} className="rounded-lg border border-border-strong px-3 py-1.5 text-fg-muted">
                  Cancel
                </button>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
