/**
 * Small formatting helpers for the Schema Intel Catalog UI.
 */

export function classNames(
  ...parts: Array<string | false | null | undefined>
): string {
  return parts.filter(Boolean).join(" ");
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function classificationColor(label: string): string {
  switch (label) {
    case "PII":
      return "bg-red-500/10 text-red-300 border-red-500/30";
    case "Sensitive":
      return "bg-amber-500/10 text-amber-300 border-amber-500/30";
    default:
      return "bg-emerald-500/10 text-emerald-300 border-emerald-500/30";
  }
}

export function methodLabel(method: string): string {
  switch (method) {
    case "value_pattern":
      return "content match";
    case "manual_override":
      return "manual";
    default:
      return "name match";
  }
}
