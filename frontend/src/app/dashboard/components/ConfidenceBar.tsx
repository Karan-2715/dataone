"use client";

interface ConfidenceBarProps {
  value: number; // 0–100
  label?: string;
  showValue?: boolean;
  size?: "sm" | "md";
  className?: string;
}

/**
 * ConfidenceBar — thin horizontal bar showing AI match confidence.
 * Colour scales: <40 red, 40-69 amber, 70+ emerald.
 */
export function ConfidenceBar({
  value,
  label,
  showValue = true,
  size = "sm",
  className = "",
}: ConfidenceBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const color =
    clamped < 40
      ? "bg-red-500"
      : clamped < 70
        ? "bg-amber-500"
        : "bg-emerald-500";
  const height = size === "sm" ? "h-1.5" : "h-2";

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {label && <span className="text-xs text-fg-subtle whitespace-nowrap">{label}</span>}
      <div className={`flex-1 rounded-full bg-surface-overlay ${height}`} role="meter" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={`rounded-full ${height} transition-all duration-300 ${color}`}
          style={{ width: `${clamped}%` }}
        />
      </div>
      {showValue && (
        <span className="text-xs font-medium tabular-nums text-fg-muted min-w-[2.5rem] text-right">
          {clamped}%
        </span>
      )}
    </div>
  );
}