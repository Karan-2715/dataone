import { describe, expect, it } from "vitest";
import { detailLevelFor, shouldAnimateEdges, visibleColumnLimit } from "../graphLevelOfDetail";

describe("topology level of detail", () => {
  it("reduces detail as the user zooms out", () => {
    expect(detailLevelFor(1, 20)).toBe("detail");
    expect(detailLevelFor(0.7, 20)).toBe("compact");
    expect(detailLevelFor(0.3, 20)).toBe("overview");
  });

  it("forces lower detail for very large graphs", () => {
    expect(detailLevelFor(1, 500)).toBe("compact");
    expect(detailLevelFor(1, 2000)).toBe("overview");
  });

  it("maps levels to bounded column rendering", () => {
    expect(visibleColumnLimit("overview")).toBe(0);
    expect(visibleColumnLimit("compact")).toBe(3);
    expect(visibleColumnLimit("detail")).toBe(8);
  });

  it("disables animation when distant or edge-heavy", () => {
    expect(shouldAnimateEdges(1, 20)).toBe(true);
    expect(shouldAnimateEdges(0.5, 20)).toBe(false);
    expect(shouldAnimateEdges(1, 301)).toBe(false);
  });
});
