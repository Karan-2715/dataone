export interface SearchableGraphNode {
  id: string;
  label: string;
  database: string;
  group: string;
  columns?: Array<{ name: string }>;
}

export interface GraphSearchMatch {
  node: SearchableGraphNode;
  matchedColumn?: string;
}

export function searchGraphNodes(nodes: SearchableGraphNode[], query: string): GraphSearchMatch[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const matches: Array<GraphSearchMatch & { rank: number }> = [];
  for (const node of nodes) {
    const label = node.label.toLowerCase();
    const database = node.database.toLowerCase();
    const column = node.columns?.find((item) => item.name.toLowerCase().includes(needle));
    if (label === needle) matches.push({ node, rank: 0 });
    else if (label.startsWith(needle)) matches.push({ node, rank: 1 });
    else if (label.includes(needle)) matches.push({ node, rank: 2 });
    else if (column) matches.push({ node, matchedColumn: column.name, rank: 3 });
    else if (database.includes(needle)) matches.push({ node, rank: 4 });
  }
  return matches
    .sort((a, b) => a.rank - b.rank || a.node.label.localeCompare(b.node.label))
    .slice(0, 12)
    .map((match) => ({ node: match.node, matchedColumn: match.matchedColumn }));
}
