"use client";
import { classNames, formatRelativeTime } from "../lib/format";

interface DraftBarProps {
  dirty: boolean;
  saving: boolean;
  lastSavedAt: string | null;
  error: string | null;
}

export default function DraftBar({ dirty, saving, lastSavedAt, error }: DraftBarProps) {
  let label: string;
  let tone: string;
  if (error) {
    label = `Autosave error: ${error}`;
    tone = "bg-danger/10 text-danger border-danger/20";
  } else if (saving) {
    label = "Saving…";
    tone = "bg-info/10 text-info border-info/20";
  } else if (dirty) {
    label = "Unsaved changes";
    tone = "bg-warning/10 text-warning border-warning/20";
  } else if (lastSavedAt) {
    label = `Saved ${formatRelativeTime(lastSavedAt)}`;
    tone = "bg-success/10 text-success border-success/20";
  } else {
    label = "No edits yet";
    tone = "bg-surface-overlay text-fg-subtle border-border-strong";
  }
  return (
    <div
      className="flex items-center justify-between border-b border-border bg-glass-bg px-5 py-1.5 text-[11px]"
      role="status"
      aria-live="polite"
    >
      <span className={classNames("px-2 py-0.5 rounded border font-medium", tone)}>
        {label}
      </span>
      <span className="text-fg-subtle">
        Autosave every 30s · also saves on tab hide
      </span>
    </div>
  );
}
