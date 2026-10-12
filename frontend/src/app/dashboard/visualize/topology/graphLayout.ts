/* ────────────────────── Height-aware auto-layout (E02-1) ──────────────────────
   The backend emitted fixed x + y = 80*(i+1) positions, which stacked
   200–400px-tall table nodes only 80px apart — they overlapped into an
   unreadable pile. We ignore the backend x/y and lay the graph out
   client-side by node group instead.

   dagre/elkjs are deliberately NOT used here: this is an edge-sparse,
   group-defined bipartite view (a source column vs a target column), so a
   connectivity-driven DAG layout would collapse the many unconnected tables
   into a single rank and break the source→target reading. A grouped,
   height-aware two-column layout preserves that reading and cannot overlap.
   Connectivity layout (dagre) is deferred to the true multi-hop lineage
   tasks (E02-10/E02-11).

   Pure and dependency-free so it can be unit-tested — see __tests__/graphLayout.test.ts.
   NB: this file is NOT named layout.ts — that is a reserved App Router segment file. */

export interface LayoutNode {
  id: string;
  group: string; // "source" | "target"
  columns?: unknown[];
  /** E02-4: node has indicators (sensitivity, drift, DQ) — adds indicator row height */
  hasIndicators?: boolean;
  /** Number of wrapped indicator rows rendered inside the bounded node width. */
  indicatorRows?: number;
  /** E02-4: node has a lineage confidence bar — adds footer bar height */
  hasConfidenceBar?: boolean;
}

export const NODE_WIDTH = 260;
export const COLUMN_GAP = 260; // horizontal room between columns (also fits edge labels)
export const ROW_GAP = 48; // vertical breathing room between stacked nodes
export const X_SOURCE = 0;
export const X_TARGET = NODE_WIDTH + COLUMN_GAP;

// Mirrors TableNode's DOM so spacing tracks the real rendered height:
// header + indicator-row? + columns padding + up to 8 rows (+ a "more" row)
// + footer + confidence-bar?
export function estimateNodeHeight(
  columnCount: number,
  indicatorRows: number | boolean = false,
  hasConfidenceBar = false,
): number {
  const HEADER = 44;
  const INDICATOR_ROW = 28; // sensitivity/drift/DQ badges row (px-3 py-1.5 flex gap-2)
  const COLS_PADDING = 16;
  const ROW = 24;
  const MORE_ROW = 20;
  const FOOTER = 30;
  const CONFIDENCE_BAR = 28; // ConfidenceBar + pb-2 padding

  const count = Math.max(columnCount, 0);
  const shown = Math.min(count, 8);
  const more = count > 8 ? MORE_ROW : 0;
  const renderedIndicatorRows = typeof indicatorRows === "number"
    ? Math.max(0, indicatorRows)
    : indicatorRows ? 1 : 0;
  return (
    HEADER +
    renderedIndicatorRows * INDICATOR_ROW +
    COLS_PADDING +
    shown * ROW +
    more +
    FOOTER +
    (hasConfidenceBar ? CONFIDENCE_BAR : 0)
  );
}

export function layoutNodes(
  nodes: LayoutNode[],
): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  let ySource = 0;
  let yTarget = 0;
  for (const n of nodes) {
    const height = estimateNodeHeight(
      (n.columns || []).length,
      n.indicatorRows ?? n.hasIndicators,
      n.hasConfidenceBar,
    );
    if (n.group === "target") {
      positions[n.id] = { x: X_TARGET, y: yTarget };
      yTarget += height + ROW_GAP;
    } else {
      positions[n.id] = { x: X_SOURCE, y: ySource };
      ySource += height + ROW_GAP;
    }
  }
  return positions;
}
