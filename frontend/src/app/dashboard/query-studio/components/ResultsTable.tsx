"use client";
import { QueryExecuteResult } from "../lib/types";

export default function ResultsTable({
  result,
  page,
  onPageChange,
}: {
  result: QueryExecuteResult;
  page: number;
  onPageChange: (page: number) => void;
}) {
  if (result.error) {
    return (
      <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20 text-red-400 text-xs font-mono">
        {result.error}
      </div>
    );
  }

  if (!result.executed) {
    return null;
  }

  if (result.affected_rows !== null && result.affected_rows !== undefined) {
    return (
      <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-emerald-400 text-xs">
        {result.statement_type.toUpperCase()} executed — {result.affected_rows} row(s) affected
        {result.duration_ms != null ? ` in ${result.duration_ms}ms` : ""}.
      </div>
    );
  }

  if (result.rows.length === 0) {
    return <div className="text-xs text-fg0 p-3">Query returned no rows.</div>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-surface-elevated">
              {result.columns.map((c) => (
                <th key={c} className="p-2 text-left font-semibold text-fg-subtle border-b border-border">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i} className="hover:bg-surface-overlay transition-colors">
                {result.columns.map((c) => (
                  <td key={c} className="p-2 text-fg-muted font-mono border-b border-border/40">
                    {row[c] === null || row[c] === undefined ? (
                      <span className="text-fg-subtle">null</span>
                    ) : (
                      String(row[c])
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-[11px] text-fg0">
        <span>
          {result.row_count} row{result.row_count === 1 ? "" : "s"}
          {result.truncated ? " (truncated)" : ""}
          {result.duration_ms != null ? ` · ${result.duration_ms}ms` : ""}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onPageChange(Math.max(1, page - 1))}
            disabled={page === 1}
            className="px-2 py-1 bg-surface-overlay hover:bg-surface-overlay rounded disabled:opacity-40"
          >
            Prev
          </button>
          <span>Page {page}</span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={!result.has_more}
            className="px-2 py-1 bg-surface-overlay hover:bg-surface-overlay rounded disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
