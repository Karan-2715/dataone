"use client";
import { AuditEvent } from "../lib/types";

/** Sequence-ordered trace of every event sharing a correlation_id. */
export default function CorrelationTimeline({
  events,
  isLoading,
  activeId,
  onJump,
}: {
  events: AuditEvent[];
  isLoading: boolean;
  activeId: number;
  onJump: (event: AuditEvent) => void;
}) {
  if (isLoading) {
    return <div className="text-xs text-fg-subtle">Loading trace…</div>;
  }
  if (events.length <= 1) {
    return <div className="text-xs text-fg-subtle">No other events share this correlation ID.</div>;
  }

  return (
    <div className="flex flex-col gap-2">
      {events.map((ev, i) => (
        <div key={ev.id} className="flex items-start gap-3">
          <div className="flex flex-col items-center pt-1">
            <div
              className={`w-2.5 h-2.5 rounded-full ${
                ev.id === activeId ? "bg-info" : "bg-surface-overlay"
              }`}
            />
            {i < events.length - 1 && <div className="w-px flex-1 bg-surface-overlay mt-1" style={{ minHeight: 20 }} />}
          </div>
          <button
            onClick={() => onJump(ev)}
            className={`flex-1 text-left rounded-lg px-3 py-2 text-xs border transition-colors ${
              ev.id === activeId
                ? "border-info/40 bg-info/10"
                : "border-border hover:bg-surface-overlay"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-fg-muted font-medium">{ev.event_type}</span>
              <span className="text-fg-subtle font-mono">{new Date(ev.created_at).toLocaleTimeString()}</span>
            </div>
            {ev.summary && <div className="text-fg-subtle mt-0.5">{ev.summary}</div>}
          </button>
        </div>
      ))}
    </div>
  );
}
