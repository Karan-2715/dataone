import { describe, it, expect } from "vitest";
import {
  estimateNodeHeight,
  layoutNodes,
  X_SOURCE,
  X_TARGET,
  ROW_GAP,
  type LayoutNode,
} from "../graphLayout";

function node(id: string, group: string, cols = 0): LayoutNode {
  return {
    id,
    group,
    columns: Array.from({ length: cols }, (_, i) => ({ name: `c${i}` })),
  };
}

describe("estimateNodeHeight", () => {
  it("grows with column count", () => {
    expect(estimateNodeHeight(2)).toBeLessThan(estimateNodeHeight(6));
  });

  it("adds a 'more' row beyond the 8-row display cap", () => {
    expect(estimateNodeHeight(20)).toBeGreaterThan(estimateNodeHeight(8));
  });

  it("never returns a non-positive height, even for 0 or negative columns", () => {
    expect(estimateNodeHeight(0)).toBeGreaterThan(0);
    expect(estimateNodeHeight(-5)).toBe(estimateNodeHeight(0));
  });

  it("reserves rendered space for indicator and lineage-confidence rows", () => {
    const base = estimateNodeHeight(4);
    expect(estimateNodeHeight(4, true, false)).toBeGreaterThan(base);
    expect(estimateNodeHeight(4, true, true)).toBeGreaterThan(
      estimateNodeHeight(4, true, false),
    );
  });

  it("reserves additional height when indicators wrap to a second row", () => {
    expect(estimateNodeHeight(4, 2, false)).toBeGreaterThan(
      estimateNodeHeight(4, 1, false),
    );
  });
});

describe("layoutNodes", () => {
  it("places sources and targets in separate columns", () => {
    const pos = layoutNodes([node("s1", "source", 3), node("t1", "target", 3)]);
    expect(pos.s1.x).toBe(X_SOURCE);
    expect(pos.t1.x).toBe(X_TARGET);
    expect(X_TARGET).toBeGreaterThan(X_SOURCE);
  });

  // This is the E02-1 regression: tall nodes must never overlap.
  it("never overlaps stacked nodes within a column", () => {
    const nodes = [
      node("s1", "source", 2),
      node("s2", "source", 9), // tall (triggers the 'more' row)
      node("s3", "source", 5),
    ];
    const pos = layoutNodes(nodes);
    for (let i = 1; i < nodes.length; i++) {
      const prev = nodes[i - 1];
      const prevBottom =
        pos[prev.id].y + estimateNodeHeight((prev.columns || []).length);
      const current = pos[nodes[i].id].y;
      expect(current).toBeGreaterThanOrEqual(prevBottom);
      expect(current - prevBottom).toBe(ROW_GAP);
    }
  });

  it("stacks the two columns independently", () => {
    const pos = layoutNodes([
      node("s1", "source", 3),
      node("t1", "target", 3),
      node("s2", "source", 3),
    ]);
    expect(pos.s2.y).toBeGreaterThan(pos.s1.y); // second source below first
    expect(pos.t1.y).toBe(0); // target column unaffected by source stacking
  });

  it("returns an empty map for no nodes", () => {
    expect(layoutNodes([])).toEqual({});
  });
});
