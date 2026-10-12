"use client";
import Link from "next/link";

interface DashboardWidgetProps {
  title: string;
  icon?: string;
  isLoading: boolean;
  isEmpty: boolean;
  isError: boolean;
  errorMessage?: string;
  emptyMessage?: string;
  emptyAction?: { label: string; href: string };
  onRetry?: () => void;
  linkUrl?: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * DashboardWidget — self-contained widget container with isolated states
 * (dashboard_tasks #3, TRD FR6). State precedence: loading > error > empty
 * > content, so exactly one state renders at a time.
 */
export function DashboardWidget({
  title,
  icon,
  isLoading,
  isEmpty,
  isError,
  errorMessage,
  emptyMessage,
  emptyAction,
  onRetry,
  linkUrl,
  className = "",
  children,
}: DashboardWidgetProps) {
  const header = (
    <h3 className="font-semibold text-fg-muted flex items-center gap-2">
      {icon && (
        <span role="img" aria-hidden="true">
          {icon}
        </span>
      )}
      {title}
    </h3>
  );

  return (
    <div
      className={`p-5 rounded-2xl bg-surface-elevated border backdrop-blur-sm ${
        isError ? "border-red-500/30" : "border-border"
      } ${className}`}
    >
      <div className="mb-4 flex items-center justify-between">
        {linkUrl && !isLoading && !isError ? (
          <Link href={linkUrl} className="hover:underline">
            {header}
          </Link>
        ) : (
          header
        )}
      </div>

      {isLoading ? (
        <div aria-hidden="true" className="flex flex-col gap-2">
          <div className="h-4 w-3/4 rounded bg-surface-overlay animate-pulse" />
          <div className="h-4 w-1/2 rounded bg-surface-overlay animate-pulse" />
          <div className="h-4 w-2/3 rounded bg-surface-overlay animate-pulse" />
        </div>
      ) : isError ? (
        <div className="text-sm text-red-400">
          <p>{errorMessage || "Failed to load data."}</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-2 px-3 py-1.5 text-xs font-semibold rounded-lg border border-red-500/30 text-red-300 hover:bg-red-500/10 transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      ) : isEmpty ? (
        <div className="text-xs text-fg0">
          <p>{emptyMessage || "No data available."}</p>
          {emptyAction && (
            <Link
              href={emptyAction.href}
              className="mt-1 inline-block text-blue-400 hover:text-blue-300"
            >
              {emptyAction.label} →
            </Link>
          )}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
