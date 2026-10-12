"use client";
import { SavedQuery } from "../lib/types";

export default function SavedQueriesPanel({
  queries,
  onLoad,
  onDelete,
}: {
  queries: SavedQuery[];
  onLoad: (q: SavedQuery) => void;
  onDelete: (id: number) => void;
}) {
  if (queries.length === 0) {
    return <div className="text-xs text-fg0 p-3">No saved queries yet.</div>;
  }
  return (
    <div className="flex flex-col gap-1">
      {queries.map((q) => (
        <div
          key={q.id}
          className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg hover:bg-surface-overlay group"
        >
          <button
            onClick={() => onLoad(q)}
            className="flex-1 text-left text-xs text-fg-muted truncate"
            title={q.sql_text}
          >
            {q.name}
          </button>
          <button
            onClick={() => onDelete(q.id)}
            className="text-fg-subtle hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity"
            aria-label={`Delete ${q.name}`}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
