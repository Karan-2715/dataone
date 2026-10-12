"use client";

import { type ReactNode } from "react";

export type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "accent"
  | "neutral";
export type BadgeSize = "sm" | "md" | "lg";

interface BadgeProps {
  children: ReactNode;
  variant?: BadgeVariant;
  size?: BadgeSize;
  className?: string;
  /** Show a pulsing dot indicator before the text */
  dot?: boolean;
  /** Numeric count to display as a badge counter */
  count?: number;
  title?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  default: "bg-surface-overlay text-fg-muted border-border",
  success: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
  warning: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
  danger: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30",
  info: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30",
  accent: "bg-accent-soft text-accent border-accent/30",
  neutral: "bg-surface-overlay text-fg-subtle border-border-strong",
};

const sizeStyles: Record<BadgeSize, string> = {
  sm: "text-[10px] px-1.5 py-0.5 gap-1",
  md: "text-xs px-2.5 py-1 gap-1.5",
  lg: "text-sm px-3 py-1.5 gap-2",
};

const dotColors: Record<BadgeVariant, string> = {
  default: "bg-fg-muted",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
  info: "bg-blue-500",
  accent: "bg-accent",
  neutral: "bg-fg-subtle",
};

/**
 * Shared Badge primitive — status labels, counters, and metadata tags.
 * Supports dot indicators for live status and numeric counters.
 */
export function Badge({
  children,
  variant = "default",
  size = "md",
  className = "",
  dot = false,
  count,
  title,
}: BadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-full border font-medium leading-none",
        variantStyles[variant],
        sizeStyles[size],
        className,
      ].join(" ")}
      title={title}
    >
      {dot && (
        <span
          className={[
            "w-1.5 h-1.5 rounded-full",
            dotColors[variant],
            variant === "success" ? "animate-pulse" : "",
          ].join(" ")}
          aria-hidden="true"
        />
      )}
      <span className="truncate max-w-[200px]">{children}</span>
      {count !== undefined && (
        <span className="ml-0.5 rounded-full bg-surface-overlay px-1.5 text-[10px] font-semibold tabular-nums">
          {count >= 1000 ? `${(count / 1000).toFixed(1)}k` : count}
        </span>
      )}
    </span>
  );
}