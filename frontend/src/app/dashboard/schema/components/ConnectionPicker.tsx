"use client";
import type { ConnectorRef } from "../lib/types";

interface ConnectionPickerProps {
  connections: ConnectorRef[];
  loading: boolean;
  connectionId: number | null;
  onChange: (id: number) => void;
}

export default function ConnectionPicker({
  connections, loading, connectionId, onChange,
}: ConnectionPickerProps) {
  if (loading) {
    return <div className="text-xs text-fg-subtle">Loading connections…</div>;
  }
  if (connections.length === 0) {
    return <div className="text-xs text-fg-subtle">No connections configured yet.</div>;
  }
  return (
    <label className="flex items-center gap-2 text-xs text-fg-subtle">
      Connection
      <select
        value={connectionId ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-2 text-sm text-fg focus:border-accent focus:outline-none"
        aria-label="Select connection"
      >
        {connections.map((c) => (
          <option key={c.id} value={c.id}>{c.name} ({c.type})</option>
        ))}
      </select>
    </label>
  );
}
