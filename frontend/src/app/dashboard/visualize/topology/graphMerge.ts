interface MergeNode {
  id: string;
  group: "source" | "target";
}

interface MergeEdge {
  source: string;
  target: string;
  edge_type?: string;
}

export function countMatchedPairs(edges: MergeEdge[]): number {
  return new Set(
    edges
      .filter((edge) => edge.edge_type !== "missing")
      .map((edge) => `${edge.source}|${edge.target}`),
  ).size;
}

interface MergeAnnotation {
  node_id: string;
}

export interface PairGraph<TNode extends MergeNode, TEdge extends MergeEdge, TAnnotation extends MergeAnnotation> {
  sourceId: number;
  targetId: number;
  graph: { nodes: TNode[]; edges: TEdge[]; annotations?: TAnnotation[] };
}

export function mergePairGraphs<TNode extends MergeNode, TEdge extends MergeEdge, TAnnotation extends MergeAnnotation>(
  pairs: Array<PairGraph<TNode, TEdge, TAnnotation>>,
): { nodes: TNode[]; edges: TEdge[]; annotations: TAnnotation[] } {
  const nodes = new Map<string, TNode>();
  const edges: TEdge[] = [];
  const annotations: TAnnotation[] = [];

  for (const pair of pairs) {
    const qualify = (nodeId: string, group: "source" | "target") =>
      `${group === "source" ? pair.sourceId : pair.targetId}:${nodeId}`;
    const groupByOriginalId = new Map(pair.graph.nodes.map((node) => [node.id, node.group]));

    for (const node of pair.graph.nodes) {
      const id = qualify(node.id, node.group);
      if (!nodes.has(id)) nodes.set(id, { ...node, id } as TNode);
    }
    for (const edge of pair.graph.edges) {
      const sourceGroup = groupByOriginalId.get(edge.source);
      const targetGroup = groupByOriginalId.get(edge.target);
      if (!sourceGroup || !targetGroup) continue;
      edges.push({
        ...edge,
        source: qualify(edge.source, sourceGroup),
        target: qualify(edge.target, targetGroup),
      } as TEdge);
    }
    for (const annotation of pair.graph.annotations ?? []) {
      const group = groupByOriginalId.get(annotation.node_id);
      if (group) annotations.push({ ...annotation, node_id: qualify(annotation.node_id, group) } as TAnnotation);
    }
  }
  return { nodes: [...nodes.values()], edges, annotations };
}
