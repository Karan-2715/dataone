export interface ClusterableNode {
  id: string;
  group: string;
  database: string;
  risk_level?: string;
  columns?: unknown[];
  is_placeholder?: boolean;
}

export interface ClusterableEdge {
  source: string;
  target: string;
  label?: string;
  edge_type?: string;
  confidence?: number;
}

export interface GroupSummaryNode extends ClusterableNode {
  is_group_summary: true;
  label: string;
  table_count: number;
  column_count: number;
  risk_counts: Record<string, number>;
}

export function groupKey(node: ClusterableNode): string {
  return `${node.group}:${node.database}`;
}

export function groupNodeId(key: string): string {
  return `group:${key}`;
}

export function clusterGraph<TNode extends ClusterableNode, TEdge extends ClusterableEdge>(
  nodes: TNode[],
  edges: TEdge[],
  collapsedGroups: ReadonlySet<string>,
): { nodes: Array<TNode | GroupSummaryNode>; edges: ClusterableEdge[] } {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const grouped = new Map<string, TNode[]>();
  for (const node of nodes) {
    const key = groupKey(node);
    const members = grouped.get(key) ?? [];
    members.push(node);
    grouped.set(key, members);
  }

  const visibleNodes: Array<TNode | GroupSummaryNode> = [];
  const endpoint = new Map<string, string>();
  for (const [key, members] of grouped) {
    if (!collapsedGroups.has(key)) {
      visibleNodes.push(...members);
      members.forEach((member) => endpoint.set(member.id, member.id));
      continue;
    }
    const id = groupNodeId(key);
    const riskCounts: Record<string, number> = {};
    members.forEach((member) => {
      endpoint.set(member.id, id);
      if (member.is_placeholder) return;
      const risk = member.risk_level ?? "unknown";
      riskCounts[risk] = (riskCounts[risk] ?? 0) + 1;
    });
    visibleNodes.push({
      id,
      group: members[0].group,
      database: members[0].database,
      label: members[0].database,
      columns: [],
      is_group_summary: true,
      table_count: members.filter((member) => !member.is_placeholder).length,
      column_count: members.reduce(
        (sum, member) => sum + (member.is_placeholder ? 0 : (member.columns?.length ?? 0)),
        0,
      ),
      risk_counts: riskCounts,
    });
  }

  const aggregate = new Map<string, ClusterableEdge & { count: number }>();
  for (const edge of edges) {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) continue;
    const source = endpoint.get(edge.source);
    const target = endpoint.get(edge.target);
    if (!source || !target || source === target) continue;
    const key = `${source}|${target}|${edge.edge_type ?? "mapping"}`;
    const current = aggregate.get(key);
    if (current) {
      current.count += 1;
      current.confidence = Math.max(current.confidence ?? 0, edge.confidence ?? 0);
    } else {
      aggregate.set(key, { ...edge, source, target, count: 1 });
    }
  }

  return {
    nodes: visibleNodes,
    edges: [...aggregate.values()].map(({ count, ...edge }) => ({
      ...edge,
      label: count > 1 ? `${count} relationships` : edge.label,
    })),
  };
}
