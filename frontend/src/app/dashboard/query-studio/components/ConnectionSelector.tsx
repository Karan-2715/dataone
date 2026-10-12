"use client";
import { Connection } from "../lib/types";

export default function ConnectionSelector({
  connections,
  value,
  onChange,
}: {
  connections: Connection[];
  value: number | null;
  onChange: (id: number) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-fg-subtle">
      <span className="hidden sm:inline">Connection</span>
      <select
        aria-label="Connection"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-1.5 text-xs text-fg focus:border-accent focus:outline-none"
      >
        <option value="" disabled>Select a connection…</option>
        {connections.map((c) => (
          <option key={c.id} value={c.id}>{c.name} ({c.type})</option>
        ))}
      </select>
    </label>
  );
}
