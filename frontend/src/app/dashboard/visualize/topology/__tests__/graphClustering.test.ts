import { describe, expect, it } from "vitest";
import { clusterGraph, groupNodeId } from "../graphClustering";

const nodes = [
  { id: "s1", group: "source", database: "CRM", risk_level: "high", columns: [1, 2] },
  { id: "s2", group: "source", database: "CRM", risk_level: "low", columns: [1] },
  { id: "t1", group: "target", database: "DW", risk_level: "low", columns: [1, 2, 3] },
];
const edges = [
  { source: "s1", target: "t1", edge_type: "exact", confidence: 100 },
  { source: "s2", target: "t1", edge_type: "exact", confidence: 90 },
];

describe("clusterGraph", () => {
  it("keeps table nodes unchanged when groups are expanded", () => {
    const result = clusterGraph(nodes, edges, new Set());
    expect(result.nodes.map((node) => node.id)).toEqual(["s1", "s2", "t1"]);
    expect(result.edges).toHaveLength(2);
  });

  it("replaces a collapsed group with a real summary node", () => {
    const result = clusterGraph(nodes, edges, new Set(["source:CRM"]));
    const summary = result.nodes.find((node) => node.id === groupNodeId("source:CRM"));
    expect(summary).toMatchObject({ table_count: 2, column_count: 3 });
    expect(summary && "risk_counts" in summary ? summary.risk_counts : {}).toEqual({ high: 1, low: 1 });
    expect(result.nodes.some((node) => node.id === "s1")).toBe(false);
  });

  it("excludes missing placeholders from collapsed counts", () => {
    const withPlaceholder = [
      ...nodes,
      { id: "missing", group: "source", database: "CRM", risk_level: "high", columns: [], is_placeholder: true },
    ];
    const result = clusterGraph(withPlaceholder, edges, new Set(["source:CRM"]));
    const summary = result.nodes.find((node) => node.id === groupNodeId("source:CRM"));
    expect(summary).toMatchObject({ table_count: 2, column_count: 3, risk_counts: { high: 1, low: 1 } });
  });

  it("aggregates duplicate relationships onto collapsed endpoints", () => {
    const result = clusterGraph(nodes, edges, new Set(["source:CRM"]));
    expect(result.edges).toEqual([expect.objectContaining({
      source: groupNodeId("source:CRM"), target: "t1", label: "2 relationships",
      confidence: 100,
    })]);
  });

  it("drops internal edges when both endpoints collapse into one group", () => {
    const internal = [{ source: "s1", target: "s2", edge_type: "business_rule" }];
    expect(clusterGraph(nodes, internal, new Set(["source:CRM"])).edges).toEqual([]);
  });
});
