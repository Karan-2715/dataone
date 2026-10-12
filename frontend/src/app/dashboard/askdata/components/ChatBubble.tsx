"use client";
import { useState } from "react";
import { ChatTurn } from "../lib/types";

export default function ChatBubble({ turn, connectionId, onEditInSql }: { turn: ChatTurn; connectionId: number | null; onEditInSql: (connectionId: number, sql: string) => void }) {
  const [showSql, setShowSql] = useState(false);
  const isUser = turn.role === "user";
  const res = turn.response;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4 animate-in slide-in-from-bottom-2 fade-in duration-300`}>
      <div
        className={`max-w-[85%] rounded-2xl px-5 py-4 ${
          isUser
            ? "bg-surface-elevated border border-border text-fg"
            : "bg-surface border border-border text-fg"
        }`}
      >
        {!isUser && (
          <div className="flex items-center gap-2 mb-3">
            <div className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-background">
              <svg className="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
            </div>
            <span className="text-[12px] font-semibold text-fg">AskData</span>
            <span className="text-[10px] text-fg-subtle ml-auto font-mono">{turn.timestamp}</span>
          </div>
        )}
        <div className="text-[14px] leading-relaxed whitespace-pre-wrap">{turn.content}</div>

        {res?.sql && (
          <div className="mt-4">
            <button
              onClick={() => setShowSql((s) => !s)}
              className="text-[11px] uppercase tracking-wider font-semibold text-fg-subtle hover:text-fg transition-colors flex items-center gap-1.5"
            >
              <svg className={`w-3 h-3 transition-transform ${showSql ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
              {showSql ? "Hide SQL" : "View Generated SQL"}
            </button>
            {showSql && (
              <pre className="mt-3 text-[12px] font-mono text-fg-subtle bg-background border border-border rounded-xl p-4 overflow-x-auto whitespace-pre-wrap">
                {res.sql}
              </pre>
            )}
          </div>
        )}

        {res?.executed && res.rows.length > 0 && (
          <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-background">
            <table className="w-full text-[12px] border-collapse">
              <thead>
                <tr className="bg-surface-elevated border-b border-border">
                  {res.columns.map((c) => (
                    <th key={c} className="px-3 py-2 text-left font-semibold text-fg-subtle">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {res.rows.slice(0, 20).map((row, i) => (
                  <tr key={i} className="hover:bg-surface-elevated transition-colors border-b border-border/50 last:border-0">
                    {res.columns.map((c) => (
                      <td key={c} className="px-3 py-2 text-fg font-mono">{String(row[c] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {res.row_count > 20 && (
              <div className="p-2 text-center text-[10px] text-fg-subtle uppercase tracking-widest font-semibold bg-surface-elevated border-t border-border">
                Showing 20 of {res.row_count} rows
              </div>
            )}
          </div>
        )}

        {res?.masked_columns && res.masked_columns.length > 0 && (
          <div className="mt-3 flex items-center gap-2 text-[11px] text-fg bg-surface-overlay border border-border px-3 py-1.5 rounded-lg">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
            Masked PII Columns: <span className="font-mono">{res.masked_columns.join(", ")}</span>
          </div>
        )}
        {res && !res.grounded && res.executed && (
          <div className="mt-3 flex items-center gap-2 text-[11px] text-fg bg-surface-overlay border border-border px-3 py-1.5 rounded-lg">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
            Not grounded in Schema Intel catalog.
          </div>
        )}

        {res?.sql && connectionId != null && (
          <div className="mt-4 flex justify-end">
            <button
              onClick={() => onEditInSql(connectionId, res.sql as string)}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-md border border-border bg-background hover:bg-surface-elevated text-fg transition-all"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
              Open in SQL Editor
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
