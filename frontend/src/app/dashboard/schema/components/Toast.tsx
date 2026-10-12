"use client";
import { classNames } from "../lib/format";

interface ToastProps {
  toast: { kind: "success" | "error"; message: string } | null;
  onDismiss: () => void;
}

export default function Toast({ toast, onDismiss }: ToastProps) {
  if (!toast) return null;
  const tone =
    toast.kind === "error"
      ? "bg-red-500/10 border-red-500/30 text-red-300"
      : "bg-emerald-500/10 border-emerald-500/30 text-emerald-300";
  const icon = toast.kind === "error" ? "⚠️" : "✅";
  return (
    <div
      role="status"
      aria-live="polite"
      className={classNames(
        "fixed top-[calc(var(--header-height)+1rem)] right-4 z-50 max-w-sm rounded-lg border px-4 py-3 text-sm shadow-lg backdrop-blur",
        tone,
      )}
    >
      <div className="flex items-start gap-2">
        <span aria-hidden>{icon}</span>
        <span className="flex-1">{toast.message}</span>
        <button
          type="button"
          aria-label="Dismiss notification"
          onClick={onDismiss}
          className="ml-2 text-xs opacity-70 hover:opacity-100"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
