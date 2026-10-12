"use client";

export type SeverityLevel = "critical" | "high" | "medium" | "low" | "none";

interface SeverityChipProps {
  level: SeverityLevel;
  label?: string;
  size?: "sm" | "md";
  className?: string;
}

const config: Record<
  SeverityLevel,
  { bg: string; text: string; dot: string; defaultLabel: string }
> = {
  critical: {
    bg: "bg-red-500/10",
    text: "text-red-600 dark:text-red-400",
    dot: "bg-red-500",
    defaultLabel: "Critical",
  },
  high: {
    bg: "bg-orange-500/10",
    text: "text-orange-600 dark:text-orange-400",
    dot: "bg-orange-500",
    defaultLabel: "High",
  },
  medium: {
    bg: "bg-amber-500/10",
    text: "text-amber-600 dark:text-amber-400",
    dot: "bg-amber-500",
    defaultLabel: "Medium",
  },
  low: {
    bg: "bg-neutral-500/10",
    text: "text-neutral-600 dark:text-neutral-400",
    dot: "bg-neutral-500",
    defaultLabel: "Low",
  },
  none: {
    bg: "bg-surface-overlay",
    text: "text-fg-subtle",
    dot: "bg-fg-subtle",
    defaultLabel: "None",
  },
};

/**
 * SeverityChip — compact inline severity indicator with a coloured dot.
 * Used on topology nodes, risk items, and DQ scorecards.
 */
export function SeverityChip({
  level,
  label,
  size = "sm",
  className = "",
}: SeverityChipProps) {
  const c = config[level];
  const sizeClasses = size === "sm" ? "text-xs px-2 py-0.5 gap-1.5" : "text-sm px-2.5 py-1 gap-2";

  return (
    <span
      className={[
        "inline-flex items-center rounded-full font-medium border border-transparent",
        c.bg,
        c.text,
        sizeClasses,
        className,
      ].join(" ")}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} aria-hidden="true" />
      {label || c.defaultLabel}
    </span>
  );
}