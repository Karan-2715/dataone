"use client";

import { useState, useEffect, useCallback } from "react";
import { api, ApiError } from "@/lib/api";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  Handle,
  Position,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { layoutNodes } from "./graphLayout";
import { clusterGraph, groupKey, type GroupSummaryNode } from "./graphClustering";
import { searchGraphNodes, type GraphSearchMatch } from "./graphSearch";
import { detailLevelFor, shouldAnimateEdges, visibleColumnLimit, type DetailLevel } from "./graphLevelOfDetail";
import { countMatchedPairs, mergePairGraphs } from "./graphMerge";
import { filterGraphByView, filterGraphByTables, withRenderedHeightFlags } from "./graphView";
import { Card } from "@/app/dashboard/components/Card";
import { Badge } from "@/app/dashboard/components/Badge";
import { SeverityChip } from "@/app/dashboard/components/SeverityChip";
import { ConfidenceBar } from "@/app/dashboard/components/ConfidenceBar";
import { EmptyState, LoadingState, ErrorState } from "@/app/dashboard/components/StateViews";
import { WorkspaceHeader } from "@/app/dashboard/components/WorkspaceHeader";

/* ─── Types ─── */
interface GraphColumn {
  name: string;
  type: string;
  primary_key?: boolean;
  classification?: { label?: string; level?: string };
}

interface GraphNodeData {
  id: string;
  label: string;
  group: "source" | "target";
  database: string;
  column_count: number;
  risk_level: "high" | "medium" | "low";
  has_issues?: boolean;
  columns?: GraphColumn[];
  /** Node indicators (E02-4) — sensitivity, drift, refresh */
  sensitivity?: "critical" | "high" | "medium" | "low";
  drift_status?: "drifted" | "stable" | "unknown";
  last_refresh?: string;
  lineage_confidence?: number; // 0-100
  lineage_confirmed?: boolean;
  /** DQ score (from E06 — render "—" until then) */
  dq_score?: number | null;
  detail_level?: DetailLevel;
  is_placeholder?: boolean;
}

interface GraphEdgeData {
  source: string;
  target: string;
  label?: string;
  style?: Record<string, string>;
  /** Edge type for v3 5-color scheme (E02-5) */
  edge_type?: "exact" | "ai" | "transformation" | "missing" | "business_rule";
  confidence?: number;
}

interface GraphAnnotation {
  type: "error" | "warning";
  severity: "high" | "medium";
  message: string;
  node_id: string;
}

interface GraphSummary {
  total_source_tables?: number;
  total_target_tables?: number;
  matched_tables?: number;
  total_annotations?: number;
}

interface GraphData {
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
  summary?: GraphSummary;
  annotations?: GraphAnnotation[];
}

interface ConnectorRef {
  id: number;
  name: string;
}

/* ─── v3 5-color edge scheme (E02-5) ─── */
const EDGE_STYLES: Record<string, { stroke: string; label: string; dash?: string }> = {
  exact: { stroke: "var(--edge-exact)", label: "Exact Match" },
  ai: { stroke: "var(--edge-ai)", label: "AI Suggested" },
  transformation: { stroke: "var(--edge-transformation)", label: "Transformation" },
  missing: { stroke: "var(--edge-missing)", label: "Missing" },
  business_rule: { stroke: "var(--edge-business-rule)", label: "Business Rule" },
};
const DEFAULT_EDGE_STYLE = { stroke: "var(--accent)", label: "Mapping" };

/* ─── Custom Table Node with E01 primitives (E02-3 + E02-4) ─── */
function TableNode({ data }: { data: GraphNodeData }) {
  const riskColors: Record<string, { border: string; bg: string }> = {
    high: { border: "var(--danger)", bg: "color-mix(in srgb, var(--danger) 8%, transparent)" },
    medium: { border: "var(--warning)", bg: "color-mix(in srgb, var(--warning) 7%, transparent)" },
    low: { border: "var(--success)", bg: "color-mix(in srgb, var(--success) 7%, transparent)" },
  };
  const r = riskColors[data.risk_level] || riskColors.low;
  const detailLevel = data.detail_level ?? "detail";
  const columnLimit = visibleColumnLimit(detailLevel);
  const hasNodeIndicators = Boolean(
    data.sensitivity || data.drift_status || data.last_refresh ||
    (data.dq_score !== undefined && data.dq_score !== null),
  );

  return (
    <div
      className="rounded-xl border-2 w-[var(--node-max-width)] shadow-2xl backdrop-blur-md"
      style={{ borderColor: r.border, background: "var(--glass-bg-strong)" }}
    >
      <Handle type="target" position={Position.Left} style={{ background: r.border }} />
      <Handle type="source" position={Position.Right} style={{ background: r.border }} />

      {/* Header with risk-level border accent */}
      <div className="px-4 py-2.5 flex items-center justify-between gap-2 border-b border-border/60" style={{ background: r.bg }}>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base shrink-0">{data.is_placeholder ? "🚫" : data.group === "source" ? "📤" : "📥"}</span>
          <span className="text-sm font-bold text-fg tracking-tight truncate" title={data.label}>{data.label}</span>
        </div>
        <SeverityChip
          level={data.risk_level === "high" ? "critical" : data.risk_level}
          label={data.risk_level.charAt(0).toUpperCase() + data.risk_level.slice(1)}
          size="sm"
          className="shrink-0"
        />
      </div>

      {/* Node indicator row (E02-4); hidden only at distant overview zoom. */}
      {detailLevel !== "overview" && hasNodeIndicators && <div className="px-3 py-1.5 flex items-center gap-2 border-b border-border/40 flex-wrap">
        {data.sensitivity && (
          <Badge
            variant={data.sensitivity === "critical" || data.sensitivity === "high" ? "danger" : "info"}
            size="sm"
            dot
          >
            {data.sensitivity}
          </Badge>
        )}
        {data.drift_status === "drifted" && (
          <Badge variant="warning" size="sm" dot>
            Drifted
          </Badge>
        )}
        {data.drift_status === "stable" && (
          <Badge variant="success" size="sm" dot>
            Stable
          </Badge>
        )}
        {data.dq_score !== undefined && data.dq_score !== null && (
          <Badge
            variant={data.dq_score >= 80 ? "success" : data.dq_score >= 50 ? "warning" : "danger"}
            size="sm"
          >
            DQ {data.dq_score}%
          </Badge>
        )}
        {data.last_refresh && (
          <Badge variant="neutral" size="sm">
            <span title={data.last_refresh}>Refreshed</span>
          </Badge>
        )}
      </div>}

      {/* Columns */}
      {columnLimit > 0 && <div className="px-3 py-2 flex flex-col gap-1">
        {(data.columns || []).slice(0, columnLimit).map((col: GraphColumn, i: number) => {
          const cls = col.classification || {};
          return (
            <div key={i} className="flex items-center justify-between gap-2 text-[11px] px-1.5 py-0.5 rounded hover:bg-surface-overlay transition-colors">
              <span className="flex items-center gap-1.5 min-w-0">
                {col.primary_key && <span className="shrink-0 text-[9px] text-warning">🔑</span>}
                <span className="font-mono text-fg-muted truncate" title={col.name}>{col.name}</span>
              </span>
              <span className="flex items-center gap-1.5 shrink-0">
                <span className="text-fg-subtle font-mono">{col.type}</span>
                {cls.level && (
                  <span className={`text-[9px] font-semibold ${cls.level === "High" ? "text-danger" : cls.level === "Medium" ? "text-warning" : "text-fg-subtle"}`}>
                    {cls.label || cls.level}
                  </span>
                )}
              </span>
            </div>
          );
        })}
        {(data.columns || []).length > columnLimit && (
          <div className="text-[10px] text-fg-subtle text-center mt-1">+{(data.columns ?? []).length - columnLimit} more columns</div>
        )}
      </div>}

      {/* Footer */}
      <div className="px-3 py-1.5 border-t border-border/40 flex items-center justify-between">
        <span className="text-[10px] text-fg-subtle">{data.database}</span>
        <span className="text-[10px] text-fg-subtle">{data.column_count} cols</span>
      </div>

      {/* Confidence bar (E02-4) */}
      {data.lineage_confidence !== undefined && data.lineage_confidence > 0 && (
        <div className="px-3 pb-2">
          <ConfidenceBar value={data.lineage_confidence} label="Lineage" size="sm" />
        </div>
      )}
      {data.lineage_confirmed && data.lineage_confidence === undefined && (
        <Badge variant="success" size="sm">Lineage confirmed</Badge>
      )}

      {data.has_issues && (
        <div className="absolute -right-2 -top-2 flex h-5 w-5 animate-pulse items-center justify-center rounded-full bg-danger text-[10px] font-bold text-white shadow-lg">!</div>
      )}
    </div>
  );
}

function SchemaGroupNode({ data }: { data: GroupSummaryNode }) {
  return (
    <Card variant="glass" padding="md" className="w-[var(--node-max-width)] border-2 border-accent/50 shadow-2xl">
      <Handle type="target" position={Position.Left} className="!bg-accent" />
      <Handle type="source" position={Position.Right} className="!bg-accent" />
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-bold text-fg" title={data.database}>🗂️ {data.database}</p>
          <p className="text-[10px] uppercase tracking-wide text-fg-subtle">{data.group} schema group</p>
        </div>
        <Badge variant="info" size="sm">Collapsed</Badge>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg bg-surface-overlay p-2"><strong className="block text-fg">{data.table_count}</strong><span className="text-fg-subtle">tables</span></div>
        <div className="rounded-lg bg-surface-overlay p-2"><strong className="block text-fg">{data.column_count}</strong><span className="text-fg-subtle">columns</span></div>
      </div>
      <div className="mt-2 flex gap-1.5">
        {Object.entries(data.risk_counts).map(([risk, count]) => <Badge key={risk} variant={risk === "high" ? "danger" : risk === "medium" ? "warning" : "success"} size="sm">{count} {risk}</Badge>)}
      </div>
    </Card>
  );
}

const nodeTypes = { tableNode: TableNode, groupNode: SchemaGroupNode };

/* ─── Main Page ─── */
export default function TopologyPage() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNodeData | null>(null);
  const [viewMode, setViewMode] = useState<"all" | "source" | "target">("all");
  const [connections, setConnections] = useState<ConnectorRef[]>([]);
  const [connectionsError, setConnectionsError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [targetIds, setTargetIds] = useState<number[]>([]);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphWarning, setGraphWarning] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [reactFlow, setReactFlow] = useState<ReactFlowInstance | null>(null);
  const [nodeSearch, setNodeSearch] = useState("");
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [viewportZoom, setViewportZoom] = useState(1);
  const [tableFilter, setTableFilter] = useState<Set<string>>(new Set());
  const [tableFilterQuery, setTableFilterQuery] = useState("");
  const [legendCollapsed, setLegendCollapsed] = useState(false);
  const [annotationsCollapsed, setAnnotationsCollapsed] = useState(false);

  const fetchConnections = useCallback(async () => {
    try {
      setConnectionsError(null);
      const list = await api.get<ConnectorRef[]>("/api/v1/connectors/");
      const data = Array.isArray(list) ? list : [];
      setConnections(data);
      if (data.length >= 2) {
        setSourceId(data[0].id);
        setTargetIds([data[1].id]);
      } else if (data.length === 1) {
        setSourceId(data[0].id);
      }
    } catch (err) {
      console.error("Connections fetch failed:", err);
      setConnectionsError(
        err instanceof ApiError ? err.message : "Unable to load database connections.",
      );
    }
  }, []);

  const fetchGraph = useCallback(async () => {
    if (sourceId == null || targetIds.length === 0) return;
    try {
      setLoading(true);
      setGraphError(null);
      setGraphWarning(null);
      const settled = await Promise.allSettled(targetIds.map(async (targetId) => ({
        sourceId,
        targetId,
        graph: await api.get<GraphData>(
          `/api/v1/schema/graph?source_id=${sourceId}&target_id=${targetId}`,
        ),
      })));
      const successful = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      const failures = settled.length - successful.length;
      if (successful.length === 0) {
        const firstFailure = settled.find((result) => result.status === "rejected");
        throw firstFailure && firstFailure.status === "rejected" ? firstFailure.reason : new Error("No graph returned");
      }
      const merged = mergePairGraphs(successful);
      setGraphData({
        ...merged,
        summary: {
          total_source_tables: merged.nodes.filter((node) => node.group === "source" && !node.is_placeholder).length,
          total_target_tables: merged.nodes.filter((node) => node.group === "target" && !node.is_placeholder).length,
          matched_tables: countMatchedPairs(merged.edges),
          total_annotations: merged.annotations.length,
        },
      });
      if (failures) setGraphWarning(`${failures} target system${failures === 1 ? "" : "s"} could not be loaded; showing available topology.`);
      setCollapsedGroups(new Set());
      setTableFilter(new Set());
      setTableFilterQuery("");
    } catch (err) {
      console.error("Graph fetch failed:", err);
      const detail =
        err instanceof ApiError
          ? err.message
          : "Please check the backend connection.";
      setGraphError(`Failed to load graph. ${detail}`);
      setGraphData(null);
    } finally {
      setLoading(false);
    }
  }, [sourceId, targetIds]);

  useEffect(() => { fetchConnections(); }, [fetchConnections]);
  useEffect(() => {
    if (sourceId != null && targetIds.length > 0) {
      fetchGraph();
    }
  }, [sourceId, targetIds, fetchGraph]);

  // Convert graph data to ReactFlow format
  const modeGraph = filterGraphByView(
    graphData?.nodes || [], graphData?.edges || [], viewMode,
  );
  const tableGraph = filterGraphByTables(modeGraph.nodes, modeGraph.edges, tableFilter);
  const modeNodes = tableGraph.nodes;
  const modeEdges = tableGraph.edges;
  const clustered = clusterGraph(modeNodes, modeEdges, collapsedGroups);
  const visibleNodes = clustered.nodes;
  const detailLevel = detailLevelFor(viewportZoom, visibleNodes.length);
  const nodePositions = layoutNodes(withRenderedHeightFlags(visibleNodes, detailLevel));
  const rfNodes = visibleNodes.map((n) => ({
    id: n.id,
    type: "is_group_summary" in n ? "groupNode" : "tableNode",
    position: nodePositions[n.id],
    data: "is_group_summary" in n ? n : { ...n, detail_level: detailLevel },
    selected: n.id === focusedNodeId,
  }));

  const rfEdges = clustered.edges
    .map((e, i: number) => {
      const edgeType = e.edge_type as GraphEdgeData["edge_type"];
      const edgeStyle = EDGE_STYLES[edgeType || ""] || DEFAULT_EDGE_STYLE;
      const strokeColor = edgeStyle.stroke;
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        label: e.label || edgeStyle.label,
        animated: edgeType !== "missing" && shouldAnimateEdges(viewportZoom, clustered.edges.length),
        markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: strokeColor },
        style: {
          strokeWidth: edgeType === "missing" ? 1.5 : 2,
          stroke: strokeColor,
          ...(edgeType === "ai" ? { strokeDasharray: "6 3" } : {}),
          ...(edgeType === "missing" ? { strokeDasharray: "4 4" } : {}),
          ...(edgeType === "transformation" ? { strokeDasharray: "8 4" } : {}),
        },
        // Readable label pill
        labelStyle: { fill: "var(--fg)", fontSize: 11, fontWeight: 700 },
        labelBgStyle: {
          fill: "var(--surface)",
          fillOpacity: 0.95,
          stroke: strokeColor,
          strokeWidth: 1,
        },
        labelBgPadding: [8, 5] as [number, number],
        labelBgBorderRadius: 6,
      };
    });

  const summary = graphData?.summary || {};
  const annotations = graphData?.annotations || [];
  const schemaGroups = Array.from(new Map(
    (graphData?.nodes || []).map((node) => [groupKey(node), {
      key: groupKey(node), group: node.group, database: node.database,
      count: (graphData?.nodes || []).filter((candidate) => groupKey(candidate) === groupKey(node)).length,
    }]),
  ).values());
  const searchMatches = searchGraphNodes(graphData?.nodes || [], nodeSearch);
  const tableOptions = (graphData?.nodes || [])
    .filter((node) => !node.is_placeholder)
    .map((node) => ({ id: node.id, label: node.label, database: node.database, group: node.group }));
  const filteredTableOptions = tableFilterQuery.trim()
    ? tableOptions.filter((option) =>
        `${option.label} ${option.database}`.toLowerCase().includes(tableFilterQuery.trim().toLowerCase()),
      )
    : tableOptions;

  const focusGraphNode = (node: GraphNodeData, fit = true) => {
    setViewMode("all");
    setCollapsedGroups((current) => {
      const next = new Set(current);
      next.delete(groupKey(node));
      return next;
    });
    setFocusedNodeId(node.id);
    setSelectedNode(node);
    if (fit) {
      window.setTimeout(() => {
        reactFlow?.fitView({ nodes: [{ id: node.id }], duration: 450, padding: 1.5, maxZoom: 1.15 });
      }, 50);
    }
  };

  const focusSearchMatch = (match: GraphSearchMatch) => {
    focusGraphNode(match.node as GraphNodeData);
  };

  const resetGraphView = () => {
    setViewMode("all");
    setCollapsedGroups(new Set());
    setNodeSearch("");
    setTableFilter(new Set());
    setTableFilterQuery("");
    setFocusedNodeId(null);
    setSelectedNode(null);
    window.setTimeout(() => reactFlow?.fitView({ duration: 450, padding: 0.15 }), 50);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <LoadingState label="Building database graph…" className="max-w-xs" />
      </div>
    );
  }

  if (connectionsError) {
    return (
      <div className="flex h-full p-8">
        <ErrorState
          title="Failed to load connections"
          message={connectionsError}
          onRetry={fetchConnections}
        />
      </div>
    );
  }

  if (sourceId == null || targetIds.length === 0) {
    return (
      <div className="flex h-full p-8">
        <EmptyState
          icon="🌐"
          title="Select connections to visualize"
          description="Pick a source and a target connection above to build the topology graph."
          action={
            connections.length < 2 ? (
              <p className="text-xs text-fg-muted">
                {connections.length === 0
                  ? "No connections available — add one on the Connectors tab."
                  : "Only one connection exists — add another to compare."}
              </p>
            ) : undefined
          }
        />
      </div>
    );
  }

  if (graphError) {
    return (
      <div className="flex h-full p-8">
        <ErrorState
          title="Failed to load graph"
          message={graphError}
          onRetry={fetchGraph}
        />
      </div>
    );
  }

  return (
    <div className="workspace-page flex h-full flex-col">
      {/* Toolbar */}
      <WorkspaceHeader
        eyebrow="Topology & Lineage"
        title="Database Topology"
        description="Explore table relationships, data risks, and grounded mapping evidence across connected systems."
        className="shrink-0 border-b border-border bg-glass-bg-strong px-4 py-4 backdrop-blur-xl md:px-6"
        actions={<div className="flex flex-wrap items-center gap-2">
          {/* B02: docked in the toolbar instead of floating over the canvas —
              target-system nodes render on the right by the layout's
              left(source)/right(target) convention, the same corner a
              floating overlay would occupy, so the two always competed for
              the same space. */}
          <div className="relative w-56">
            <label className="glass flex items-center gap-2 rounded-xl border border-border-strong px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-accent/50">
              <span aria-hidden="true">🔎</span>
              <span className="sr-only">Search topology nodes and columns</span>
              <input
                value={nodeSearch}
                onChange={(event) => setNodeSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && searchMatches[0]) focusSearchMatch(searchMatches[0]);
                  if (event.key === "Escape") setNodeSearch("");
                }}
                placeholder="Find table or column…"
                className="min-w-0 flex-1 bg-transparent text-xs text-fg outline-none placeholder:text-fg-subtle"
              />
              {nodeSearch && <button type="button" onClick={() => setNodeSearch("")} aria-label="Clear topology search" className="text-xs text-fg-subtle">×</button>}
            </label>
            {nodeSearch.trim() && (
              <div className="glass-strong absolute right-0 top-full z-30 mt-1 max-h-72 w-72 overflow-y-auto rounded-xl border border-border-strong p-1 shadow-2xl">
                {searchMatches.length === 0 ? <p className="p-3 text-xs text-fg-subtle">No matching table or column.</p> : searchMatches.map((match) => (
                  <button key={match.node.id} type="button" onClick={() => focusSearchMatch(match)} className="flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left hover:bg-surface-overlay">
                    <span className="min-w-0"><span className="block truncate text-xs font-semibold text-fg">{match.node.label}</span><span className="block truncate text-[10px] text-fg-subtle">{match.node.database} · {match.node.group}{match.matchedColumn ? ` · ${match.matchedColumn}` : ""}</span></span>
                    <span className="text-[10px] text-fg-subtle">Focus</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <label className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-fg-subtle">
            📤 Source
            <select
              value={sourceId ?? ""}
              onChange={(e) => {
                const nextSource = e.target.value === "" ? null : Number(e.target.value);
                setSourceId(nextSource);
                if (nextSource !== null) setTargetIds((current) => current.filter((id) => id !== nextSource));
              }}
              className="px-2 py-1.5 text-xs font-semibold rounded-lg bg-surface-overlay text-fg-muted border border-border-strong hover:bg-surface-overlay focus:outline-none focus:ring-2 focus:ring-accent/50"
            >
              <option value="">Select…</option>
              {connections.map((c: ConnectorRef) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </label>
          <details className="relative">
            <summary className="cursor-pointer rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-[10px] font-bold uppercase tracking-wider text-fg-subtle">
              📥 Targets ({targetIds.length})
            </summary>
            <div className="glass-strong absolute right-0 top-9 z-30 min-w-56 rounded-xl border border-border-strong p-2 shadow-2xl">
              {connections.filter((connection) => connection.id !== sourceId).map((connection) => (
                <label key={connection.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-xs text-fg-muted hover:bg-surface-overlay">
                  <input type="checkbox" checked={targetIds.includes(connection.id)} onChange={(event) => setTargetIds((current) => event.target.checked ? [...current, connection.id] : current.filter((id) => id !== connection.id))} />
                  {connection.name}
                </label>
              ))}
            </div>
          </details>
          {/* Table-level filter — narrows the graph to specific tables within
              the selected source/target schemas (distinct from the connection
              pickers above, which choose the schemas themselves). */}
          <details className="relative">
            <summary className="cursor-pointer rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-[10px] font-bold uppercase tracking-wider text-fg-subtle">
              🔠 Tables {tableFilter.size > 0 ? `(${tableFilter.size})` : "(All)"}
            </summary>
            <div className="glass-strong absolute right-0 top-9 z-30 w-64 rounded-xl border border-border-strong p-2 shadow-2xl">
              <div className="mb-2 flex items-center gap-2">
                <input
                  value={tableFilterQuery}
                  onChange={(event) => setTableFilterQuery(event.target.value)}
                  placeholder="Filter tables…"
                  aria-label="Filter table list"
                  className="min-w-0 flex-1 rounded-lg border border-border-strong bg-surface-overlay px-2 py-1 text-xs text-fg outline-none placeholder:text-fg-subtle"
                />
                {tableFilter.size > 0 && (
                  <button type="button" onClick={() => setTableFilter(new Set())} className="shrink-0 text-[10px] font-semibold text-fg-subtle hover:text-accent">
                    Clear
                  </button>
                )}
              </div>
              <div className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
                {filteredTableOptions.length === 0 ? (
                  <p className="p-2 text-xs text-fg-subtle">No matching tables.</p>
                ) : filteredTableOptions.map((option) => (
                  <label key={option.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-fg-muted hover:bg-surface-overlay">
                    <input
                      type="checkbox"
                      checked={tableFilter.has(option.id)}
                      onChange={(event) => setTableFilter((current) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(option.id); else next.delete(option.id);
                        return next;
                      })}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{option.group === "source" ? "📤" : "📥"} {option.label}</span>
                      <span className="block truncate text-[10px] text-fg-subtle">{option.database}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
          </details>
          <span className="ml-1 self-center text-[10px] font-bold uppercase tracking-wider text-fg-subtle">View</span>
          {(["all", "source", "target"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                viewMode === mode
                  ? "bg-accent text-accent-fg"
                  : "bg-surface-overlay text-fg-subtle hover:bg-surface-overlay"
              }`}
            >
              {mode === "all" ? "🌐 All" : mode === "source" ? "📤 Source" : "📥 Target"}
            </button>
          ))}
          <button type="button"
            onClick={fetchGraph}
            className="workspace-primary-action ml-1 min-h-0 rounded-lg px-4 py-1.5 text-xs"
          >
            🔄 Refresh
          </button>
          <button type="button" onClick={() => reactFlow?.fitView({ duration: 450, padding: 0.15 })} className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/50">
            Fit graph
          </button>
          <button type="button" onClick={resetGraphView} className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/50">
            Reset view
          </button>
          {schemaGroups.map((schemaGroup) => {
            const collapsed = collapsedGroups.has(schemaGroup.key);
            return (
              <button
                key={schemaGroup.key}
                type="button"
                onClick={() => setCollapsedGroups((current) => {
                  const next = new Set(current);
                  if (next.has(schemaGroup.key)) next.delete(schemaGroup.key); else next.add(schemaGroup.key);
                  return next;
                })}
                aria-pressed={collapsed}
                className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-1.5 text-xs font-semibold text-fg-muted hover:border-accent/50"
                title={`${collapsed ? "Expand" : "Collapse"} ${schemaGroup.database}`}
              >
                {collapsed ? "▸" : "▾"} {schemaGroup.database} ({schemaGroup.count})
              </button>
            );
          })}
        </div>}
      />

      <div className="flex flex-1 flex-col overflow-hidden xl:flex-row">
        {/* Graph Canvas */}
        <div className="relative min-h-[28rem] flex-1">
          {graphWarning && <div role="status" className="absolute left-1/2 top-4 z-30 -translate-x-1/2 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">{graphWarning}</div>}
          {visibleNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <EmptyState
                icon="🌐"
                title="No schema objects found"
                description="The selected connections returned no tables. Scan or populate a schema, then refresh the topology."
              />
            </div>
          ) : <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            fitView
            onInit={setReactFlow}
            onMove={(_, viewport) => setViewportZoom(viewport.zoom)}
            onlyRenderVisibleElements
            onNodeClick={(_, node) => {
              if ("is_group_summary" in node.data) {
                setCollapsedGroups((current) => {
                  const next = new Set(current);
                  next.delete(`${node.data.group}:${node.data.database}`);
                  return next;
                });
              } else {
                focusGraphNode(node.data as GraphNodeData, false);
              }
            }}
            attributionPosition="bottom-right"
            minZoom={0.3}
            maxZoom={2}
          >
            <Background color="var(--border)" gap={20} size={1} />
            <Controls className="!bg-surface !border-border !text-fg-subtle !rounded-lg !shadow-xl" />
          </ReactFlow>}

          <Badge variant="neutral" size="sm" className="absolute bottom-4 right-4 z-10">
            {detailLevel === "overview" ? "Overview" : detailLevel === "compact" ? "Compact" : "Detailed"} · {visibleNodes.length} nodes
          </Badge>

          {/* Legend Overlay */}
          <Card variant="glass" padding="sm" className={`absolute top-4 left-4 shadow-2xl ${legendCollapsed ? "w-auto" : "w-44"}`}>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-bold text-fg-subtle uppercase tracking-wider">
                  Edge Legend
                </span>
                <button
                  type="button"
                  onClick={() => setLegendCollapsed((value) => !value)}
                  aria-expanded={!legendCollapsed}
                  aria-label={legendCollapsed ? "Expand edge legend" : "Minimize edge legend"}
                  className="shrink-0 text-xs leading-none text-fg-subtle hover:text-accent"
                >
                  {legendCollapsed ? "▸" : "▾"}
                </button>
              </div>
              {!legendCollapsed && <>
              {Object.entries(EDGE_STYLES).map(([key, style]) => (
                <div key={key} className="flex items-center gap-2 text-[11px] text-fg-subtle">
                  <svg width="16" height="4" viewBox="0 0 16 4" className="shrink-0">
                    <line
                      x1="0" y1="2" x2="16" y2="2"
                      stroke={style.stroke}
                      strokeWidth="2"
                      strokeDasharray={
                        key === "ai" ? "6 3" :
                        key === "missing" ? "4 4" :
                        key === "transformation" ? "8 4" : undefined
                      }
                    />
                  </svg>
                  {style.label}
                </div>
              ))}
              <div className="flex items-center gap-2 text-[11px] text-fg-subtle mt-1 pt-1 border-t border-border/40">
                <div className="h-3 w-3 shrink-0 rounded-sm bg-danger" />
                High Risk
              </div>
              <div className="flex items-center gap-2 text-[11px] text-fg-subtle">
                <div className="h-3 w-3 shrink-0 rounded-sm bg-warning" />
                Medium Risk
              </div>
              <div className="flex items-center gap-2 text-[11px] text-fg-subtle">
                <div className="h-3 w-3 shrink-0 rounded-sm bg-success" />
                Low Risk
              </div>
              </>}
            </div>
          </Card>

          {/* Summary Cards */}
          <div className="absolute bottom-4 left-4 flex gap-2">
            {[
              { label: "Source Tables", value: summary.total_source_tables, color: "text-info" },
              { label: "Target Tables", value: summary.total_target_tables, color: "text-accent" },
              { label: "Matched", value: summary.matched_tables, color: "text-success" },
              { label: "Issues", value: summary.total_annotations, color: "text-danger" },
            ].map((s, i) => (
              <div key={i} className="px-3 py-2 rounded-lg glass">
                <div className={`text-lg font-bold ${s.color}`}>{s.value ?? 0}</div>
                <div className="text-[10px] text-fg-subtle">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Side Panel: Annotations + Details */}
        <aside className="flex max-h-72 w-full flex-col overflow-y-auto border-t border-border bg-glass-bg xl:max-h-none xl:w-72 xl:border-l xl:border-t-0">
          <div className="p-4 border-b border-border flex items-start justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-fg">Issues & Annotations</h4>
              <p className="text-[10px] text-fg-subtle mt-0.5">{annotations.length} finding(s)</p>
            </div>
            <button
              type="button"
              onClick={() => setAnnotationsCollapsed((value) => !value)}
              aria-expanded={!annotationsCollapsed}
              aria-label={annotationsCollapsed ? "Expand issues and annotations" : "Minimize issues and annotations"}
              className="shrink-0 text-sm leading-none text-fg-subtle hover:text-accent"
            >
              {annotationsCollapsed ? "▸" : "▾"}
            </button>
          </div>
          {!annotationsCollapsed && <div className="flex flex-col gap-1.5 p-3">
            {annotations.map((a: GraphAnnotation, i: number) => (
              <button
                type="button"
                key={i}
                onClick={() => {
                  const node = graphData?.nodes.find((candidate) => candidate.id === a.node_id);
                  if (node) focusGraphNode(node);
                }}
                className={`p-2.5 rounded-lg border text-xs ${
                  a.severity === "high"
                    ? "bg-danger/5 border-danger/20 text-danger"
                    : "bg-warning/5 border-warning/20 text-warning"
                } text-left hover:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/50`}
                aria-label={`Focus ${a.node_id}: ${a.message}`}
              >
                <div className="font-semibold">{a.type === "error" ? "" : ""} {a.message}</div>
                <div className="text-[10px] opacity-60 mt-0.5">{a.node_id}</div>
              </button>
            ))}
            {annotations.length === 0 && (
              <div className="text-xs text-fg-subtle text-center py-4">No issues detected </div>
            )}
          </div>}

          {/* Selected Node Detail */}
          {selectedNode && (
            <div className="border-t border-border p-4">
              <h4 className="text-sm font-semibold text-fg mb-2"> {selectedNode.label}</h4>
              <div className="flex flex-col gap-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Database</span>
                  <span className="text-fg-muted">{selectedNode.database}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Group</span>
                  <span className="text-fg-muted capitalize">{selectedNode.group}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Columns</span>
                  <span className="text-fg-muted">{selectedNode.column_count}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Risk</span>
                  <SeverityChip
                    level={selectedNode.risk_level === "high" ? "critical" : selectedNode.risk_level}
                    label={selectedNode.risk_level.charAt(0).toUpperCase() + selectedNode.risk_level.slice(1)}
                    size="sm"
                  />
                </div>
                {selectedNode.sensitivity && (
                  <div className="flex justify-between">
                    <span className="text-fg-subtle">Sensitivity</span>
                    <Badge variant={selectedNode.sensitivity === "critical" || selectedNode.sensitivity === "high" ? "danger" : "info"} size="sm">
                      {selectedNode.sensitivity}
                    </Badge>
                  </div>
                )}
                {selectedNode.drift_status && (
                  <div className="flex justify-between">
                    <span className="text-fg-subtle">Drift</span>
                    <Badge variant={selectedNode.drift_status === "drifted" ? "warning" : "success"} size="sm">
                      {selectedNode.drift_status}
                    </Badge>
                  </div>
                )}
                {selectedNode.lineage_confidence !== undefined && (
                  <div className="mt-1">
                    <ConfidenceBar value={selectedNode.lineage_confidence} label="Lineage confidence" size="sm" />
                  </div>
                )}
                {selectedNode.lineage_confirmed && selectedNode.lineage_confidence === undefined && (
                  <div className="mt-3"><Badge variant="success">Published lineage confirmed</Badge></div>
                )}
                {selectedNode.last_refresh && (
                  <div className="flex justify-between gap-3">
                    <span className="text-fg-subtle">Last refresh</span>
                    <time className="text-right text-fg-muted" dateTime={selectedNode.last_refresh}>
                      {new Date(selectedNode.last_refresh).toLocaleString()}
                    </time>
                  </div>
                )}
                {selectedNode.columns && selectedNode.columns.length > 0 && (
                  <details className="mt-2 rounded-lg border border-border p-2">
                    <summary className="cursor-pointer text-xs font-semibold text-fg-muted">Columns ({selectedNode.columns.length})</summary>
                    <ul className="mt-2 max-h-44 space-y-1 overflow-y-auto">
                      {selectedNode.columns.map((column) => (
                        <li key={column.name} className="flex justify-between gap-2 font-mono text-[10px]">
                          <span className="truncate text-fg-muted">{column.name}</span>
                          <span className="shrink-0 text-fg-subtle">{column.type}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
