"use client";
import { AuditEvent } from "../lib/types";
import JsonViewer from "./JsonViewer";
import CorrelationTimeline from "./CorrelationTimeline";
import { useCorrelationTrace } from "../hooks/useAuditEvents";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-fg-subtle">{label}</span>
      <span className="text-sm text-fg-muted">{value ?? <span className="text-fg-subtle">—</span>}</span>
    </div>
  );
}

export default function EventDetail({
  event,
  onClose,
  onSelectEvent,
}: {
  event: AuditEvent;
  onClose: () => void;
  onSelectEvent: (event: AuditEvent) => void;
}) {
  const trace = useCorrelationTrace(event.correlation_id);

  return (
    <div
      data-testid="event-detail"
      className="glass flex flex-col gap-5 rounded-2xl p-5"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="text-lg font-semibold text-fg">{event.event_type}</div>
          <div className="text-xs text-fg-subtle font-mono">#{event.id} · seq {event.sequence ?? "—"}</div>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-fg-subtle hover:text-fg">
          Close
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Field label="Actor" value={event.actor} />
        <Field label="Module" value={event.module} />
        <Field label="Outcome" value={event.outcome} />
        <Field label="Duration" value={event.duration_ms != null ? `${event.duration_ms}ms` : null} />
        <Field label="Target Type" value={event.target_type} />
        <Field label="Target" value={event.target_name ?? event.target_id} />
        <Field label="Timestamp" value={new Date(event.created_at).toLocaleString()} />
        <Field label="Correlation ID" value={event.correlation_id} />
      </div>

      {event.summary && <Field label="Summary" value={event.summary} />}

      <div className="grid grid-cols-1 gap-3 border-t border-border pt-4">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-fg-subtle mb-1">Before</div>
          <JsonViewer data={event.before_summary} />
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-fg-subtle mb-1">After</div>
          <JsonViewer data={event.after_summary} />
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-fg-subtle mb-1">Metadata</div>
          <JsonViewer data={event.metadata} />
        </div>
      </div>

      {event.correlation_id && (
        <div className="border-t border-border pt-4">
          <div className="text-[11px] uppercase tracking-wide text-fg-subtle mb-2">Correlation Trace</div>
          <CorrelationTimeline
            events={trace.data?.events ?? []}
            isLoading={trace.isLoading}
            activeId={event.id}
            onJump={onSelectEvent}
          />
        </div>
      )}
    </div>
  );
}
