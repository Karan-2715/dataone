export type DetailLevel = "overview" | "compact" | "detail";

export function detailLevelFor(zoom: number, nodeCount: number): DetailLevel {
  if (zoom < 0.5 || nodeCount > 1500) return "overview";
  if (zoom < 0.82 || nodeCount > 400) return "compact";
  return "detail";
}

export function visibleColumnLimit(level: DetailLevel): number {
  if (level === "overview") return 0;
  if (level === "compact") return 3;
  return 8;
}

export function shouldAnimateEdges(zoom: number, edgeCount: number): boolean {
  return zoom >= 0.65 && edgeCount <= 300;
}
