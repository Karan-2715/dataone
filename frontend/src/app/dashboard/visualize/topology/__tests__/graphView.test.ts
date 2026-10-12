import { describe, expect, it } from "vitest";
import { filterGraphByTables, filterGraphByView, withRenderedHeightFlags } from "../graphView";

describe("filterGraphByView", () => {
  const nodes = [
    { id: "s", group: "source" },
    { id: "t", group: "target" },
  ];
  const edges = [{ source: "s", target: "t" }];

  it("never returns an edge with a hidden endpoint", () => {
    expect(filterGraphByView(nodes, edges, "source")).toEqual({ nodes: [nodes[0]], edges: [] });
    expect(filterGraphByView(nodes, edges, "target")).toEqual({ nodes: [nodes[1]], edges: [] });
    expect(filterGraphByView(nodes, edges, "all").edges).toEqual(edges);
  });
});

describe("filterGraphByTables", () => {
  const nodes = [
    { id: "s1", group: "source" },
    { id: "s2", group: "source" },
    { id: "t1", group: "target" },
    { id: "t2", group: "target" },
  ];
  const edges = [
    { source: "s1", target: "t1" },
    { source: "s2", target: "t2" },
  ];

  it("is a no-op for an empty selection", () => {
    expect(filterGraphByTables(nodes, edges, new Set())).toEqual({ nodes, edges });
  });

  it("keeps a selected table's connected counterpart so mappings stay visible", () => {
    const result = filterGraphByTables(nodes, edges, new Set(["s1"]));
    expect(result.nodes.map((n) => n.id).sort()).toEqual(["s1", "t1"]);
    expect(result.edges).toEqual([{ source: "s1", target: "t1" }]);
  });

  it("drops edges whose endpoints fall outside the selection", () => {
    const result = filterGraphByTables(nodes, edges, new Set(["s1"]));
    expect(result.edges.some((e) => e.source === "s2" || e.target === "t2")).toBe(false);
  });
});

describe("withRenderedHeightFlags", () => {
  it("mirrors indicator and confidence render guards used by TableNode", () => {
    const [detail] = withRenderedHeightFlags([{
      id: "s", group: "source", columns: [], sensitivity: "high", lineage_confidence: 80,
    }], "detail");
    expect(detail.hasIndicators).toBe(true);
    expect(detail.indicatorRows).toBe(1);
    expect(detail.hasConfidenceBar).toBe(true);

    const [overview] = withRenderedHeightFlags([{
      id: "s", group: "source", columns: [], sensitivity: "high", lineage_confidence: 80,
    }], "overview");
    expect(overview.hasIndicators).toBe(false);
    expect(overview.indicatorRows).toBe(0);
    expect(overview.hasConfidenceBar).toBe(true);
  });

  it("reserves an indicator row for refresh-only nodes", () => {
    const [node] = withRenderedHeightFlags([{
      id: "s", group: "source", columns: [], last_refresh: "2026-07-17T00:00:00Z",
    }], "detail");
    expect(node.hasIndicators).toBe(true);
  });

  it("accounts for indicator wrapping at four badges", () => {
    const [node] = withRenderedHeightFlags([{
      id: "s", group: "source", columns: [], sensitivity: "high",
      drift_status: "stable", dq_score: 90, last_refresh: "2026-07-17T00:00:00Z",
    }], "detail");
    expect(node.indicatorRows).toBe(2);
  });
});
