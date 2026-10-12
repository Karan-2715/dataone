import type { DetailLevel } from "./graphLevelOfDetail";
import type { LayoutNode } from "./graphLayout";

interface ViewNode {
  id: string;
  group: string;
}

interface ViewEdge {
  source: string;
  target: string;
}

export function filterGraphByView<TNode extends ViewNode, TEdge extends ViewEdge>(
  nodes: TNode[],
  edges: TEdge[],
  view: "all" | "source" | "target",
): { nodes: TNode[]; edges: TEdge[] } {
  const visibleNodes = view === "all" ? nodes : nodes.filter((node) => node.group === view);
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return {
    nodes: visibleNodes,
    edges: edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}

/**
 * Narrow the graph to a chosen set of table node ids. The selection is
 * expanded to include each selected table's directly-connected counterparts
 * so mapping edges (source↔target) remain visible instead of dangling.
 * An empty selection is a no-op (shows everything).
 */
export function filterGraphByTables<TNode extends ViewNode, TEdge extends ViewEdge>(
  nodes: TNode[],
  edges: TEdge[],
  selectedIds: ReadonlySet<string>,
): { nodes: TNode[]; edges: TEdge[] } {
  if (selectedIds.size === 0) return { nodes, edges };
  const keep = new Set<string>(selectedIds);
  for (const edge of edges) {
    if (selectedIds.has(edge.source)) keep.add(edge.target);
    if (selectedIds.has(edge.target)) keep.add(edge.source);
  }
  const visibleNodes = nodes.filter((node) => keep.has(node.id));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return {
    nodes: visibleNodes,
    edges: edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}

interface IndicatorNode extends LayoutNode {
  sensitivity?: string;
  drift_status?: string;
  dq_score?: number | null;
  lineage_confidence?: number;
  lineage_confirmed?: boolean;
  last_refresh?: string;
}

export function withRenderedHeightFlags<TNode extends IndicatorNode>(
  nodes: TNode[],
  detailLevel: DetailLevel,
): Array<TNode & { hasIndicators: boolean; indicatorRows: number; hasConfidenceBar: boolean }> {
  return nodes.map((node) => {
    const indicatorCount = [
      Boolean(node.sensitivity),
      Boolean(node.drift_status),
      node.dq_score !== undefined && node.dq_score !== null,
      Boolean(node.last_refresh),
    ].filter(Boolean).length;
    const hasIndicators = detailLevel !== "overview" && indicatorCount > 0;
    return {
      ...node,
      hasIndicators,
      indicatorRows: hasIndicators ? Math.ceil(indicatorCount / 3) : 0,
      hasConfidenceBar: node.lineage_confidence !== undefined && node.lineage_confidence > 0,
    };
  });
}
