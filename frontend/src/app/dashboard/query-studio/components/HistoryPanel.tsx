"use client";
import { HistoryEntry } from "../lib/types";

const OUTCOME_STYLES: Record<string, string> = {
  success: "text-emerald-400",
  failure: "text-red-400",
  warning: "text-amber-400",
};

export default function HistoryPanel({
  entries,
  onLoad,
}: {
  entries: HistoryEntry[];
  onLoad: (sql: string) => void;
}) {
  if (entries.length === 0) {
    return <div className="text-xs text-fg0 p-3">No queries run yet.</div>;
  }
  return (
    <div className="flex flex-col gap-1">
      {entries.map((e) => (
        <button
          key={e.id}
          onClick={() => e.sql && onLoad(e.sql)}
          className="text-left px-3 py-2 rounded-lg hover:bg-surface-overlay flex flex-col gap-0.5"
        >
          <div className="text-xs text-fg-muted font-mono truncate">{e.sql ?? "—"}</div>
          <div className="flex items-center gap-2 text-[10px] text-fg0">
            <span className={OUTCOME_STYLES[e.outcome] ?? ""}>{e.outcome}</span>
            <span>{new Date(e.created_at).toLocaleString()}</span>
            {e.row_count != null && <span>{e.row_count} rows</span>}
          </div>
        </button>
      ))}
    </div>
  );
}
