"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { FixedSizeList, type ListOnScrollProps } from "react-window";
import { classNames } from "../lib/format";
import type {
  FieldMapping,
  Role,
  SourceRef,
  TargetRef,
  TransformationPayload,
} from "../lib/types";

interface CanvasProps {
  mappingId: number;
  edges: FieldMapping[];
  selectedEdgeId: number | null;
  canEdit: boolean;
  role: Role | null;
  onSelectEdge: (id: number | null) => void;
  onCreateEdge: (
    target: TargetRef,
    sources: SourceRef[],
    transformation: TransformationPayload,
  ) => Promise<FieldMapping | null>;
  /** Lightweight interaction guidance, e.g. "stage a source column first" —
   * surfaced when a target column is clicked before that precondition is
   * met, instead of the click silently doing nothing. */
  onHint?: (message: string) => void;
}

interface ColumnNode {
  id: string;
  table: string;
  column: string;
  type: string;
  // True when the column accepts NULL (default). False means NOT NULL.
  // Already in the connector schema payload (e.g. jdbc.py); TRD FR1
  // requires the UI to display this in both source and target panels
  // before any mapping exists (see mapper_tasks/03_nullability_display.md).
  nullable: boolean;
  primary_key: boolean;
  edge_id?: number; // if already mapped
  side: "source" | "target";
}

interface ConnectorView {
  sourceId: string;
  targetId: string;
  edgeId: number;
  origin: FieldMapping["origin"];
  confidence: number | null;
}

/**
 * Canvas — side-by-side source/target columns with inline SVG connectors.
 * Drag a source column onto a target column to create a 1:1 edge. The UI
 * calls onCreateEdge which posts to /api/v1/mappings/{id}/edges.
 *
 * Schema metadata (table/column/type) is fetched from the existing
 * /api/v1/connectors/{id}/schema endpoint for source and target.
 */
export default function Canvas({
  mappingId,
  edges,
  selectedEdgeId,
  canEdit,
  role,
  onSelectEdge,
  onCreateEdge,
  onHint,
}: CanvasProps) {
  const [sourceColumns, setSourceColumns] = useState<ColumnNode[]>([]);
  const [targetColumns, setTargetColumns] = useState<ColumnNode[]>([]);
  const [sourceConnId, setSourceConnId] = useState<number | null>(null);
  const [targetConnId, setTargetConnId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draggingSourceId, setDraggingSourceId] = useState<string | null>(null);
  const [hoverTargetId, setHoverTargetId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // mapper_tasks #1: multi-source staging. Click a source column to add it to
  // the staging set; click again to remove. The "Connect N → target" pill
  // appears once >=1 source is staged; clicking a target with staged sources
  // calls onCreateEdge with all of them. For >=2 sources the transformation
  // defaults to `concat` (the only MULTI_SOURCE_KIND); the backend guard
  // (mapping_service.add_edge) rejects any other kind.
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // mapper_tasks #4: each panel is a react-window FixedSizeList once its
  // content exceeds the bounded viewport height (below), so it scrolls
  // internally rather than growing the page. The connector overlay draws
  // absolute-positioned SVG lines between source/target rows, so it needs
  // each panel's current scroll offset to keep lines aligned with the rows
  // actually visible — without this, virtualizing would silently misalign
  // (or draw ghost) connector lines the moment either panel scrolls.
  const [sourceScrollTop, setSourceScrollTop] = useState(0);
  const [targetScrollTop, setTargetScrollTop] = useState(0);
  // mapper_tasks #02: a visually-hidden live region announces staging and
  // edge-creation events for screen-reader users, mirroring the visual
  // "N selected" pill / connector-line feedback sighted users already get.
  const [liveMessage, setLiveMessage] = useState("");

  // Load mapping → resolve source/target connection ids, then schemas.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Staging is per-mapping state: column ids are just `src_${table}_${col}`,
    // so a selection carried across a mapping switch either applies to
    // identically-named columns in the NEW mapping (creating an edge the user
    // staged for the old one) or, if names don't collide, leaves a phantom
    // "N staged" pill with no visible rows to un-toggle
    // (review_schema_mapper_round2 #2).
    setSelectedSourceIds([]);
    (async () => {
      try {
        const { api } = await import("@/lib/api");
        const m = await api.get<{
          id: number;
          source_id: number | null;
          target_id: number | null;
        }>(`/api/v1/mappings/${mappingId}`);
        if (cancelled) return;
        setSourceConnId(m.source_id);
        setTargetConnId(m.target_id);
        if (!m.source_id || !m.target_id) {
          setLoading(false);
          return;
        }
        const [s, t] = await Promise.all([
          api.get<{ schema: Record<string, Array<{ name: string; type: string; primary_key?: boolean; nullable?: boolean }>> }>(
            `/api/v1/connectors/${m.source_id}/schema`,
          ),
          api.get<{ schema: Record<string, Array<{ name: string; type: string; primary_key?: boolean; nullable?: boolean }>> }>(
            `/api/v1/connectors/${m.target_id}/schema`,
          ),
        ]);
        if (cancelled) return;
        setSourceColumns(flattenSchema(s.schema, "source"));
        setTargetColumns(flattenSchema(t.schema, "target"));
      } catch (err) {
        if (!cancelled) {
          const message =
            err instanceof Error ? err.message : "Failed to load schema.";
          setError(message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mappingId]);

  // Mark columns that are already mapped (for visual hint).
  const targetEdgeByColumn = useMemo(() => {
    const map = new Map<string, number>();
    for (const e of edges) {
      map.set(`${e.target.table}.${e.target.column}`, e.id);
    }
    return map;
  }, [edges]);

  const sourceMappedKeys = useMemo(() => {
    const set = new Set<string>();
    for (const e of edges) {
      for (const s of e.sources) set.add(`${s.table}.${s.column}`);
    }
    return set;
  }, [edges]);

  // Render connectors between mapped columns.
  const connectors: ConnectorView[] = useMemo(() => {
    const out: ConnectorView[] = [];
    for (const e of edges) {
      for (const s of e.sources) {
        out.push({
          sourceId: `src_${s.table}_${s.column}`,
          targetId: `tgt_${e.target.table}_${e.target.column}`,
          edgeId: e.id,
          origin: e.origin,
          confidence: e.ai_confidence ?? null,
        });
      }
    }
    return out;
  }, [edges]);

  const rowHeight = 36;
  const maxRows = Math.max(sourceColumns.length, targetColumns.length);
  // Shared by both virtualized panels AND the connector overlay so they
  // stay pixel-aligned: below this height, a panel's content fits without
  // scrolling (unvirtualized in effect — react-window only windows rows
  // outside the current viewport) and the overlay draws every connector at
  // its natural position, unchanged from before #04. Above it, panels
  // scroll internally and the overlay clips lines to what's on-screen
  // (below) instead of drawing to a now-offscreen row.
  const panelHeight = Math.max(280, Math.min(560, maxRows * rowHeight + 24));

  // O(1) id -> ColumnNode lookup, mirroring the sourceMappedKeys/
  // targetEdgeByColumn Map/Set pattern already used above instead of
  // re-scanning sourceColumns per lookup.
  const sourceColumnsById = useMemo(() => {
    const map = new Map<string, ColumnNode>();
    for (const c of sourceColumns) map.set(c.id, c);
    return map;
  }, [sourceColumns]);

  const onDrop = async (target: ColumnNode) => {
    if (!canEdit || !draggingSourceId || creating) return;
    const source = sourceColumnsById.get(draggingSourceId);
    if (!source) return;
    // A drag-and-drop is a deliberate single-source action; clear any
    // staged multi-select so it can't bleed into a later target click
    // (previously a staged selection survived a drag-drop and could fire
    // an unrelated multi-source edge on the next target click).
    setSelectedSourceIds([]);
    setCreating(true);
    setDraggingSourceId(null);
    setHoverTargetId(null);
    try {
      await onCreateEdge(
        {
          table: target.table,
          column: target.column,
          type: target.type,
          nullable: target.nullable,
          primary_key: target.primary_key,
        },
        [{ table: source.table, column: source.column, type: source.type, nullable: source.nullable }],
        { kind: "direct" },
      );
      setLiveMessage(
        `Connected ${source.table}.${source.column} to ${target.table}.${target.column}.`,
      );
    } catch {
      // addEdge already toasted the error.
    } finally {
      setCreating(false);
    }
  };

  // mapper_tasks #1: connect N staged sources to a clicked target column.
  // Used by the "Connect N → target" affordance and by clicking a target
  // while sources are staged. Computes a sane default transformation:
  // 1 source → direct, 2+ sources → a space-joined concat (a reasonable
  // default for the common "merge two name-ish columns" case; the user can
  // still open the transform editor on the created edge to adjust it —
  // which this auto-selects so that affordance isn't left undiscoverable).
  const connectStagedSources = async (target: ColumnNode) => {
    if (!canEdit || creating) return;
    if (selectedSourceIds.length === 0) return;
    const sources = selectedSourceIds
      .map((id) => sourceColumnsById.get(id))
      .filter((c): c is ColumnNode => Boolean(c));
    if (sources.length === 0) return;
    setCreating(true);
    setHoverTargetId(null);
    try {
      const transformation: TransformationPayload =
        sources.length === 1
          ? { kind: "direct" }
          : {
              kind: "concat",
              parts: sources.flatMap((_, i) =>
                i === 0
                  ? [{ kind: "source" as const }]
                  : [{ kind: "literal" as const, value: " " }, { kind: "source" as const }],
              ),
            };
      const edge = await onCreateEdge(
        {
          table: target.table,
          column: target.column,
          type: target.type,
          nullable: target.nullable,
          primary_key: target.primary_key,
        },
        sources.map((s) => ({
          table: s.table,
          column: s.column,
          type: s.type,
          nullable: s.nullable,
        })),
        transformation,
      );
      setSelectedSourceIds([]);
      if (edge && sources.length > 1) onSelectEdge(edge.id);
      const sourceLabel = sources.map((s) => `${s.table}.${s.column}`).join(", ");
      setLiveMessage(`Connected ${sourceLabel} to ${target.table}.${target.column}.`);
    } catch {
      // addEdge already toasted the error.
    } finally {
      setCreating(false);
    }
  };

  const toggleSourceSelection = (id: string) => {
    if (!canEdit) return;
    setSelectedSourceIds((prev) => {
      const col = sourceColumnsById.get(id);
      const label = col ? `${col.table}.${col.column}` : id;
      if (prev.includes(id)) {
        const next = prev.filter((x) => x !== id);
        setLiveMessage(`${label} unstaged. ${next.length} source${next.length === 1 ? "" : "s"} staged.`);
        return next;
      }
      const next = [...prev, id];
      setLiveMessage(`${label} staged as source ${next.length}. ${next.length} source${next.length === 1 ? "" : "s"} staged.`);
      return next;
    });
  };

  // O(1) row-level "is this staged" checks, but carrying the staging ORDER
  // (1-based): the click sequence is the concat order, and the rows render
  // it as a numbered badge so "John Doe" vs "Doe John" is visible before
  // the edge exists (review_schema_mapper_round2 #6).
  const selectedSourceOrder = useMemo(() => {
    const map = new Map<string, number>();
    selectedSourceIds.forEach((id, i) => map.set(id, i + 1));
    return map;
  }, [selectedSourceIds]);

  const clearSelection = () => setSelectedSourceIds([]);

  // Escape clears the whole staging set — without this the only exit is
  // re-clicking every staged column one by one
  // (review_schema_mapper_round2 #8). Mounted once; a no-op functional
  // update keeps it cheap when nothing is staged.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedSourceIds((prev) => (prev.length > 0 ? [] : prev));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-auto p-5"
      aria-label="Schema mapping canvas"
    >
      {loading ? (
        <div className="flex items-center justify-center h-64 text-sm text-fg-subtle">
          Loading schemas…
        </div>
      ) : error ? (
        <div className="text-sm text-danger bg-danger/10 border border-danger/20 rounded px-3 py-2">
          {error}
        </div>
      ) : (
        <div className="flex gap-6 justify-center min-w-fit">
          <SchemaPanel
            title="Source"
            connId={sourceConnId}
            nodes={sourceColumns}
            side="source"
            mappedKeys={sourceMappedKeys}
            draggingId={draggingSourceId}
            selectedOrder={selectedSourceOrder}
            onDragStart={canEdit ? setDraggingSourceId : undefined}
            onDragEnd={() => setDraggingSourceId(null)}
            onToggleSelect={canEdit ? toggleSourceSelection : undefined}
            onClearSelection={clearSelection}
            panelHeight={panelHeight}
            rowHeight={rowHeight}
            onScroll={setSourceScrollTop}
          />
          <ConnectorOverlay
            height={panelHeight}
            connectors={connectors}
            rowHeight={rowHeight}
            sourceScrollTop={sourceScrollTop}
            targetScrollTop={targetScrollTop}
            nodeIndex={(id) => {
              const idx = sourceColumns.findIndex((n) => n.id === id);
              if (idx >= 0) return { row: idx, side: "source" as const };
              const idx2 = targetColumns.findIndex((n) => n.id === id);
              if (idx2 >= 0) return { row: idx2, side: "target" as const };
              return null;
            }}
            selectedEdgeId={selectedEdgeId}
            onSelectEdge={onSelectEdge}
          />
          <SchemaPanel
            title="Target"
            connId={targetConnId}
            nodes={targetColumns}
            side="target"
            mappedKeys={new Set(Array.from(targetEdgeByColumn.keys()))}
            draggingId={null}
            onDragStart={undefined}
            onDragEnd={undefined}
            hoverId={hoverTargetId}
            onDropHover={(id) => setHoverTargetId(id)}
            onDrop={onDrop}
            onTargetClick={canEdit ? connectStagedSources : undefined}
            onHint={canEdit ? onHint : undefined}
            stagedCount={selectedSourceIds.length}
            panelHeight={panelHeight}
            rowHeight={rowHeight}
            onScroll={setTargetScrollTop}
          />
        </div>
      )}
      {/* Accessibility live region (mapper_tasks #02): announces staging
          and edge-creation events for screen-reader users, mirroring the
          "N selected" pill / connector-line feedback sighted users get. */}
      <div className={classNames("sr-only")} role="status" aria-live="polite">
        {liveMessage}
      </div>
      {/* Legend for the row glyphs (key, NOT-NULL star, staging badges) —
          previously unexplained anywhere (review_schema_mapper_round2 #10). */}
      {!loading && !error && (
        <div className="mt-3 text-center text-[10px] text-fg-subtle">
          🔑 primary key · <span className="text-warning font-semibold">*</span> NOT NULL
          {canEdit && (
            <> · click source columns to stage them (numbers = concat order), then click a target — Esc clears</>
          )}
        </div>
      )}
      {!canEdit && role && (
        <div className="mt-4 text-center text-xs text-warning italic">
          Your role ({role}) cannot create or modify edges.
        </div>
      )}
    </div>
  );
}

function flattenSchema(
  schema: Record<string, Array<{ name: string; type: string; primary_key?: boolean; nullable?: boolean }>>,
  side: "source" | "target",
): ColumnNode[] {
  const out: ColumnNode[] = [];
  for (const table of Object.keys(schema)) {
    for (const col of schema[table]) {
      out.push({
        id: `${side === "source" ? "src" : "tgt"}_${table}_${col.name}`,
        table,
        column: col.name,
        type: col.type,
        // Default to nullable=true to match the backend's own default in
        // app/connectors/jdbc.py if a connector implementation ever omits it.
        nullable: col.nullable !== false,
        primary_key: !!col.primary_key,
        side,
      });
    }
  }
  return out;
}

function SchemaPanel({
  title,
  connId,
  nodes,
  side,
  mappedKeys,
  draggingId,
  hoverId,
  selectedOrder,
  onDragStart,
  onDragEnd,
  onDropHover,
  onDrop,
  onToggleSelect,
  onClearSelection,
  onTargetClick,
  onHint,
  stagedCount,
  panelHeight,
  rowHeight,
  onScroll,
}: {
  title: string;
  connId: number | null;
  nodes: ColumnNode[];
  side: "source" | "target";
  mappedKeys: Set<string>;
  draggingId: string | null;
  hoverId?: string | null;
  // id → 1-based staging position; the position is the concat order.
  selectedOrder?: Map<string, number>;
  onDragStart?: (id: string) => void;
  onDragEnd?: () => void;
  onDropHover?: (id: string | null) => void;
  onDrop?: (n: ColumnNode) => void;
  onToggleSelect?: (id: string) => void;
  onClearSelection?: () => void;
  onTargetClick?: (n: ColumnNode) => void;
  onHint?: (message: string) => void;
  stagedCount?: number;
  // mapper_tasks #4: bounded viewport height + row height for the
  // react-window list; onScroll reports scrollTop up to Canvas so the
  // connector overlay can stay aligned with whatever rows are on screen.
  panelHeight: number;
  rowHeight: number;
  onScroll: (scrollTop: number) => void;
}) {
  const accent = side === "source" ? "text-info" : "text-accent";
  // Tracks whether a native HTML5 drag just completed, so the onClick that
  // some browsers fire right after dragend doesn't get treated as a fresh
  // toggle. Previously checked `window.getSelection()?.toString()`, but
  // that reflects browser text-selection state, not drag state — native
  // draggable drags never populate it, so the check never caught a real
  // post-drag click, and it could false-positive on unrelated text
  // selected elsewhere on the page.
  const justDraggedRef = useRef(false);
  // mapper_tasks #1: when sources are staged and the user clicks a target
  // column, convert that click into a multi-source edge create instead of
  // a no-op. The cursor changes to a crosshair to signal this.
  const targetReadyForClick =
    side === "target" && !!onTargetClick && (stagedCount ?? 0) > 0;
  return (
    <div className="w-80 shrink-0">
      <div className={classNames("text-xs font-semibold mb-2 flex items-center gap-2", accent)}>
        <span>📥 {title}</span>
        {connId && <span className="text-fg-subtle font-normal">#{connId}</span>}
        {side === "source" && (selectedOrder?.size ?? 0) > 0 && (
          <span className="ml-auto flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-edge-business-rule/15 text-edge-business-rule border border-edge-business-rule/30">
            {selectedOrder!.size} selected
            {/* Clear-all exit for the staging set; Esc does the same
                (review_schema_mapper_round2 #8). */}
            <button
              type="button"
              onClick={onClearSelection}
              aria-label="Clear staged sources"
              title="Clear staged sources (Esc)"
              className="leading-none text-edge-business-rule hover:text-white"
            >
              ×
            </button>
          </span>
        )}
        {side === "target" && (stagedCount ?? 0) > 0 && (
          <span className="ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-success/15 text-success border border-success/30">
            {stagedCount} source{stagedCount === 1 ? "" : "s"} staged — click target
          </span>
        )}
      </div>
      <div className="flex flex-col rounded-xl border border-border bg-surface-elevated p-2 min-h-[200px]">
        {nodes.length === 0 ? (
          <div className="text-[11px] text-fg-subtle italic px-2 py-3">
            No columns.
          </div>
        ) : (
          // mapper_tasks #4: virtualized so a 1,000-column schema mounts
          // only the rows currently in view instead of the whole list.
          // Below panelHeight's cap (Canvas's panelHeight computation) this
          // renders every row anyway — small/medium schemas are pixel-
          // identical to the pre-virtualization layout, nothing changes
          // until a panel would have exceeded the bounded viewport height.
          <FixedSizeList
            height={panelHeight}
            width="100%"
            itemCount={nodes.length}
            itemSize={rowHeight}
            onScroll={(p: ListOnScrollProps) => onScroll(p.scrollOffset)}
          >
            {({ index, style }) => {
              const n = nodes[index];
              const isMapped = mappedKeys.has(`${n.table}.${n.column}`);
              const isDragging = draggingId === n.id;
              const isHover = hoverId === n.id;
              const stagedPos = selectedOrder?.get(n.id);
              const isSelected = stagedPos !== undefined;
              // Mapped columns can't participate in staging: a mapped source
              // always ends in the backend's many-to-many 422, a mapped target
              // in its double-mapped 409 — don't offer a click the server is
              // guaranteed to reject (review_schema_mapper_round2 #7).
              const stageableSource =
                side === "source" && !!onToggleSelect && !isMapped;
              // Every unmapped target responds to a click once editing is
              // allowed, not just once a source is staged — a target that
              // silently ignores a click (the prior behavior when nothing
              // was staged yet) is indistinguishable from a broken button
              // (uiux bug report: "target is not choosing the target
              // columns"). `clickableTarget` is the narrower "sources are
              // staged, this click actually connects them" case.
              const targetRespondsToClick =
                side === "target" && !!onTargetClick && !isMapped;
              const clickableTarget = targetReadyForClick && !isMapped;
              // mapper_tasks #02: exactly the rows with real mouse behavior
              // (stage / connect / guided hint) get a keyboard equivalent —
              // matching the WCAG requirement that all functionality
              // reachable by mouse is reachable by keyboard, without
              // inventing tab stops for rows that were never clickable in
              // the first place.
              const isActionable = stageableSource || targetRespondsToClick;
              const activate = () => {
                if (stageableSource) {
                  if (justDraggedRef.current) return;
                  onToggleSelect!(n.id);
                  return;
                }
                if (targetRespondsToClick) {
                  if (clickableTarget && onTargetClick) {
                    onTargetClick(n);
                  } else {
                    onHint?.(
                      "Select a source column first, then click this target to connect it.",
                    );
                  }
                }
              };
              const ariaLabel = stageableSource
                ? isSelected
                  ? `${n.table}.${n.column}, staged as source ${stagedPos}. Press Enter to unstage.`
                  : `${n.table}.${n.column}. Press Enter to stage as a source column.`
                : targetRespondsToClick
                  ? clickableTarget
                    ? `Connect ${stagedCount} staged source column${stagedCount === 1 ? "" : "s"} to ${n.table}.${n.column}.`
                    : `${n.table}.${n.column}. Stage a source column first, then press Enter here to connect it.`
                  : undefined;
              return (
              <div
                key={n.id}
                style={style}
                draggable={!!onDragStart}
                tabIndex={isActionable ? 0 : undefined}
                role={isActionable ? "button" : undefined}
                aria-pressed={stageableSource ? isSelected : undefined}
                aria-label={ariaLabel}
                onDragStart={(e) => {
                  if (onDragStart) {
                    e.dataTransfer.effectAllowed = "link";
                    justDraggedRef.current = true;
                    onDragStart(n.id);
                  }
                }}
                onDragEnd={() => {
                  onDragEnd?.();
                  // Defer clearing past the click that may follow dragend.
                  setTimeout(() => {
                    justDraggedRef.current = false;
                  }, 0);
                }}
                onDragOver={(e) => {
                  if (onDrop && side === "target") {
                    e.preventDefault();
                    onDropHover?.(n.id);
                  }
                }}
                onDragLeave={() => {
                  if (isHover) onDropHover?.(null);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  if (onDrop && side === "target") {
                    onDrop(n);
                  }
                }}
                onClick={activate}
                onKeyDown={(e) => {
                  if (!isActionable) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    activate();
                  }
                }}
                title={
                  side === "source" && isMapped && onToggleSelect
                    ? "Already mapped — a source column can map to only one target"
                    : targetReadyForClick && isMapped
                      ? "Already mapped — edit the existing edge's sources instead"
                      : targetRespondsToClick && !clickableTarget
                        ? "Select a source column first, then click this target to connect it"
                        : undefined
                }
                className={classNames(
                  "flex items-center justify-between px-2 py-1.5 rounded text-[11px] font-mono border transition-all",
                  isDragging && "opacity-50",
                  isSelected && "border-edge-business-rule/50 bg-edge-business-rule/10",
                  isMapped && !isSelected
                    ? "border-success/30 bg-success/5"
                    : "border-transparent hover:bg-surface-overlay",
                  isHover && "border-info/40 bg-info/10",
                  onDragStart ? "cursor-grab" : "",
                  stageableSource ? "cursor-pointer" : "",
                  clickableTarget ? "cursor-crosshair" : "",
                  targetRespondsToClick && !clickableTarget ? "cursor-pointer" : "",
                  // While staging, mapped targets are visibly not an option.
                  targetReadyForClick && isMapped && "opacity-40 cursor-not-allowed",
                  isActionable && "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                )}
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  {n.primary_key && (
                    <span role="img" aria-label="primary key" className="text-warning text-[9px]">🔑</span>
                  )}
                  {isSelected && (
                    // Numbered, not a checkmark: the staging position IS the
                    // concat order, so the user sees "first_name(1) last_name(2)
                    // → 'John Doe'" before the edge exists
                    // (review_schema_mapper_round2 #6).
                    <span
                      role="img"
                      aria-label={`staged as source ${stagedPos}`}
                      className="w-3.5 h-3.5 shrink-0 rounded-full bg-edge-business-rule/25 border border-edge-business-rule/50 text-edge-business-rule text-[9px] font-bold flex items-center justify-center"
                    >
                      {stagedPos}
                    </span>
                  )}
                  <span className="text-fg-subtle mr-1">{n.table}.</span>
                  <span className={classNames(side === "source" ? "text-info" : "text-accent", "truncate")}>
                    {n.column}
                  </span>
                </span>
                <span className="text-fg-subtle text-[10px]">
                  {n.type}
                  {/* TRD FR1: surface nullability in the raw schema panels.
                      NOT NULL columns get a small `*` suffix so the
                      distinction is visible before any mapping exists. */}
                  {/* role="img" so the aria-label is actually announced —
                      a bare span's aria-label is ignored by most screen
                      readers (review_schema_mapper_round2 #10). */}
                  {!n.nullable && (
                    <span
                      role="img"
                      className="ml-1 text-warning font-semibold"
                      title="NOT NULL"
                      aria-label="NOT NULL"
                    >
                      *
                    </span>
                  )}
                </span>
              </div>
              );
            }}
          </FixedSizeList>
        )}
      </div>
    </div>
  );
}

function ConnectorOverlay({
  height,
  connectors,
  rowHeight,
  sourceScrollTop,
  targetScrollTop,
  nodeIndex,
  selectedEdgeId,
  onSelectEdge,
}: {
  height: number;
  connectors: ConnectorView[];
  rowHeight: number;
  sourceScrollTop: number;
  targetScrollTop: number;
  nodeIndex: (id: string) => { row: number; side: "source" | "target" } | null;
  selectedEdgeId: number | null;
  onSelectEdge: (id: number | null) => void;
}) {
  const width = 160;
  return (
    <div className="w-40 shrink-0 relative">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold text-center mb-2">
        Mappings
      </div>
      <svg width={width} height={height} role="img" aria-label="Mapping connectors">
        {connectors.map((c) => {
          const sIdx = nodeIndex(c.sourceId);
          const tIdx = nodeIndex(c.targetId);
          if (!sIdx || !tIdx) return null;
          const sy = sIdx.row * rowHeight + 16 - sourceScrollTop;
          const ty = tIdx.row * rowHeight + 16 - targetScrollTop;
          // mapper_tasks #4: once a panel is virtualized+scrolled, a
          // connector whose endpoint has scrolled out of view would
          // otherwise draw a line to a phantom position outside the
          // visible rows. Hiding it (rather than clamping/drawing anyway)
          // is the only option that never misrepresents which columns are
          // actually connected on screen right now.
          if (sy < 0 || sy > height || ty < 0 || ty > height) return null;
          const isAi = c.origin === "ai_accepted";
          const isSelected = c.edgeId === selectedEdgeId;
          const stroke = isSelected
            ? "#60a5fa"
            : isAi
              ? "#8b5cf6"
              : "#22c55e";
          return (
            <g
              key={c.edgeId}
              style={{ cursor: "pointer" }}
              onClick={() => onSelectEdge(c.edgeId)}
            >
              <line
                x1={0}
                y1={sy}
                x2={width}
                y2={ty}
                stroke={stroke}
                strokeWidth={isSelected ? 3 : 2}
                strokeDasharray={isAi ? "5,3" : "0"}
                opacity={0.85}
              />
              {c.confidence !== null && (
                <text
                  x={width / 2}
                  y={(sy + ty) / 2 - 4}
                  textAnchor="middle"
                  fill="#a1a1aa"
                  fontSize={9}
                >
                  {Math.round(c.confidence <= 1 ? c.confidence * 100 : c.confidence)}%
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
