import { describe, expect, it } from "vitest";
import { countMatchedPairs, mergePairGraphs } from "../graphMerge";

const graph = (target: string) => ({
  nodes: [
    { id: "src_customers", group: "source" as const, label: "customers" },
    { id: `tgt_${target}`, group: "target" as const, label: target },
  ],
  edges: [{ source: "src_customers", target: `tgt_${target}`, edge_type: "exact" }],
  annotations: [{ node_id: `tgt_${target}`, message: "review" }],
});

describe("mergePairGraphs", () => {
  it("deduplicates the shared source and qualifies target IDs", () => {
    const result = mergePairGraphs([
      { sourceId: 1, targetId: 2, graph: graph("orders") },
      { sourceId: 1, targetId: 3, graph: graph("billing") },
    ]);
    expect(result.nodes.map((node) => node.id)).toEqual([
      "1:src_customers", "2:tgt_orders", "3:tgt_billing",
    ]);
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["1:src_customers", "2:tgt_orders"],
      ["1:src_customers", "3:tgt_billing"],
    ]);
  });

  it("qualifies annotation node references", () => {
    expect(mergePairGraphs([{ sourceId: 1, targetId: 2, graph: graph("orders") }]).annotations[0].node_id)
      .toBe("2:tgt_orders");
  });

  it("drops edges whose endpoint is absent", () => {
    const broken = graph("orders");
    broken.edges.push({ source: "missing", target: "tgt_orders", edge_type: "exact" });
    expect(mergePairGraphs([{ sourceId: 1, targetId: 2, graph: broken }]).edges).toHaveLength(1);
  });
});

describe("countMatchedPairs", () => {
  it("counts table pairs rather than duplicate column-level edges", () => {
    expect(countMatchedPairs([
      { source: "s", target: "t", edge_type: "ai" },
      { source: "s", target: "t", edge_type: "ai" },
      { source: "s2", target: "t2", edge_type: "missing" },
    ])).toBe(1);
  });
});
