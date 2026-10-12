"use client";

import { type ReactNode } from "react";
import { Card } from "./Card";

interface EmptyProps {
  icon?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

/**
 * Empty state — shown when a module has no data to display.
 * Never shows fake or placeholder data.
 */
export function EmptyState({ icon = "📭", title, description, action, className = "" }: EmptyProps) {
  return (
    <Card variant="glass" padding="lg" className={`text-center ${className}`}>
      <div className="flex flex-col items-center gap-3 py-8">
        <span className="text-4xl" role="img" aria-hidden="true">
          {icon}
        </span>
        <h3 className="text-lg font-semibold text-fg">{title}</h3>
        {description && <p className="text-sm text-fg-muted max-w-md">{description}</p>}
        {action && <div className="mt-2">{action}</div>}
      </div>
    </Card>
  );
}

interface LoadingProps {
  label?: string;
  className?: string;
}

/**
 * Loading skeleton — shimmer placeholder for async content.
 * Mimics the shape of a typical Card.
 */
export function LoadingState({ label = "Loading…", className = "" }: LoadingProps) {
  return (
    <Card variant="default" padding="lg" className={className} aria-label={label}>
      <div className="flex flex-col gap-4 animate-pulse" aria-hidden="true">
        <div className="h-4 w-2/3 rounded bg-surface-overlay" />
        <div className="h-8 w-1/3 rounded bg-surface-overlay" />
        <div className="h-3 w-1/2 rounded bg-surface-overlay" />
        <div className="h-3 w-3/4 rounded bg-surface-overlay" />
      </div>
      <span className="sr-only">{label}</span>
    </Card>
  );
}

interface ErrorProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

/**
 * Error state — shown when data fetching fails.
 * Includes optional retry button. Never auto-dismisses.
 */
export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  className = "",
}: ErrorProps) {
  return (
    <Card variant="default" padding="lg" className={`border-red-500/20 ${className}`}>
      <div className="flex flex-col items-center gap-3 py-6 text-center">
        <span className="text-3xl" role="img" aria-hidden="true">
          ⚠️
        </span>
        <h3 className="text-base font-semibold text-fg">{title}</h3>
        {message && <p className="text-sm text-fg-muted max-w-md">{message}</p>}
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90 transition-opacity"
          >
            Try again
          </button>
        )}
      </div>
    </Card>
  );
}

interface LoadingSpinnerProps {
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

/**
 * Minimal spinner for inline loading states (buttons, small containers).
 */
export function LoadingSpinner({ label = "Loading", size = "md", className = "" }: LoadingSpinnerProps) {
  const dims = size === "sm" ? "w-4 h-4" : size === "lg" ? "w-8 h-8" : "w-5 h-5";
  return (
    <div className={`inline-flex items-center gap-2 ${className}`} role="status" aria-label={label}>
      <svg className={`animate-spin ${dims} text-fg-muted`} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" aria-hidden="true">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span className="text-xs text-fg-muted">{label}</span>
    </div>
  );
}

interface KpiNotAvailableProps {
  label: string;
  reason?: string;
}

/**
 * Explicit "not yet available" state for KPI tiles that depend on
 * epics that haven't shipped yet (e.g. DQ score before E06, governance
 * score before E08). Per the no-mock-UI rule, never shows fake numbers.
 */
export function KpiNotAvailable({ label, reason }: KpiNotAvailableProps) {
  return (
    <Card variant="default" padding="md" className="opacity-60">
      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium text-fg-subtle">{label}</span>
        <div className="text-xl font-bold text-fg-subtle">—</div>
        <span className="text-[10px] text-fg-subtle">
          {reason || "Not yet available"}
        </span>
      </div>
    </Card>
  );
}