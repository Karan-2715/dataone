// Enterprise v2 — E01-2 Shared UI primitives
// Every epic imports from here, never restyles per-page.

export { Card } from "./Card";
export type { CardVariant, CardPadding } from "./Card";

export { Badge } from "./Badge";
export type { BadgeVariant, BadgeSize } from "./Badge";

export { SeverityChip } from "./SeverityChip";
export type { SeverityLevel } from "./SeverityChip";

export { Gauge } from "./Gauge";

export { ConfidenceBar } from "./ConfidenceBar";

export {
  EmptyState,
  LoadingState,
  ErrorState,
  LoadingSpinner,
  KpiNotAvailable,
} from "./StateViews";

export { KPITile, formatKPIValue } from "./KPITile";
export type { KPITileData, DashboardSummary, FeedItemData, TimeRange, TileStatus } from "../types";
export { default as GlobalSearchPalette } from "./GlobalSearchPalette";
export { default as NotificationCenter } from "./NotificationCenter";
export { default as CopilotPanel } from "./CopilotPanel";
export { WorkspaceHeader, SegmentedControl } from "./WorkspaceHeader";
export { DialogSurface } from "./DialogSurface";
