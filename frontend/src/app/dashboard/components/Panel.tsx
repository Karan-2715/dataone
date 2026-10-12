"use client";
import { useRef } from "react";
import type { PanelState } from "../hooks/useWorkspaceLayout";

interface PanelProps {
  title: string;
  state: PanelState;
  onStateChange: (state: PanelState) => void;
  width: number;
  onWidthChange: (width: number) => void;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
}

const WIDTH_STEP = 16;

/**
 * Dockable panel primitive (Enterprise v2, E11-2). A native, no-dependency
 * container with Maximize/Restore + a single collapse action (Minimize and
 * Close are consolidated into one "collapsed" state for this v1 — there is
 * no separate panel-reopen surface yet, so a distinct Close would have no
 * way back except the same expand affordance Minimize already offers) and
 * a pointer-drag splitter for width, resizable via the keyboard as well.
 *
 * This is a single free-floating panel, not a full docking region — E11-3
 * (drag-to-dock) and E11-5/6/7 (workspace tabs) are out of scope for this
 * slice; see the E11 spec for why.
 */
export default function Panel({
  title,
  state,
  onStateChange,
  width,
  onWidthChange,
  minWidth = 240,
  maxWidth = 640,
  children,
}: PanelProps) {
  const resizing = useRef(false);

  if (state === "collapsed") {
    return (
      <div className="w-9 shrink-0 border-l border-border bg-surface-elevated flex flex-col items-center py-2">
        <button
          type="button"
          onClick={() => onStateChange("normal")}
          aria-label={`Expand ${title} panel`}
          title={`Expand ${title}`}
          className="text-[11px] font-semibold text-fg0 hover:text-fg-muted [writing-mode:vertical-rl] rotate-180 py-2"
        >
          {title}
        </button>
      </div>
    );
  }

  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    resizing.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!resizing.current) return;
    const next = Math.min(maxWidth, Math.max(minWidth, width - e.movementX));
    onWidthChange(next);
  };
  const stopResize = (e: React.PointerEvent<HTMLDivElement>) => {
    resizing.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };
  const onResizeKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      onWidthChange(Math.min(maxWidth, width + WIDTH_STEP));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onWidthChange(Math.max(minWidth, width - WIDTH_STEP));
    }
  };

  const maximized = state === "maximized";
  const containerClass = maximized
    ? "absolute inset-0 z-20 bg-surface-elevated flex flex-col"
    : "relative shrink-0 flex flex-col border-l border-border bg-surface-elevated";

  return (
    <div
      className={containerClass}
      style={maximized ? undefined : { width }}
      role="region"
      aria-label={`${title} panel`}
    >
      {!maximized && (
        <div
          onPointerDown={startResize}
          onPointerMove={onResizeMove}
          onPointerUp={stopResize}
          onKeyDown={onResizeKeyDown}
          role="separator"
          aria-orientation="vertical"
          aria-label={`Resize ${title} panel`}
          tabIndex={0}
          className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-accent/40 focus:bg-accent/50 focus:outline-none"
        />
      )}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <span className="text-[11px] font-semibold text-fg-muted uppercase tracking-wide">{title}</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onStateChange("collapsed")}
            aria-label={`Minimize ${title} panel`}
            title="Minimize"
            className="w-5 h-5 flex items-center justify-center text-fg0 hover:text-fg-muted rounded"
          >
            ─
          </button>
          <button
            type="button"
            onClick={() => onStateChange(maximized ? "normal" : "maximized")}
            aria-label={maximized ? `Restore ${title} panel` : `Maximize ${title} panel`}
            title={maximized ? "Restore" : "Maximize"}
            className="w-5 h-5 flex items-center justify-center text-fg0 hover:text-fg-muted rounded"
          >
            □
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
