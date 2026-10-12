"use client";
import type { TimeRange } from "../types";

const OPTIONS: { value: TimeRange; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

interface TimeRangeFilterProps {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
  disabled?: boolean;
}

/**
 * TimeRangeFilter — segmented control for the dashboard time range
 * (dashboard_tasks #6). Disabled while a fetch is in flight to prevent
 * rapid switching; an unknown value falls back to "7d".
 */
export function TimeRangeFilter({ value, onChange, disabled }: TimeRangeFilterProps) {
  const safeValue = OPTIONS.some((o) => o.value === value) ? value : "7d";

  return (
    <div
      className="flex rounded-xl border border-border-strong overflow-hidden"
      role="radiogroup"
      aria-label="Time range"
    >
      {OPTIONS.map((opt) => {
        const isSelected = safeValue === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              isSelected
                ? "bg-blue-600 text-white"
                : "bg-surface-elevated text-fg-subtle hover:bg-surface-overlay"
            } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
