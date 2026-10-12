"use client";
import { useEffect, useRef, useState, useMemo } from "react";
import { api, ApiError } from "@/lib/api";
import ChatBubble from "../../askdata/components/ChatBubble";
import ConnectionPicker from "../../askdata/components/ConnectionPicker";
import SchemaDesignPlanCard from "./SchemaDesignPlanCard";
import type { AskDataAskResponse, ChatTurn, SessionMessageEntry, SessionMessagesResponse } from "../../askdata/lib/types";
import type { Connection } from "../../query-studio/lib/types";

const SUGGESTIONS = [
  "show all tables",
  "count rows",
  "show everything in the first table",
  "database health",
];

// B05 — the conversation was component-local state, discarded whenever
// this page's route unmounted (e.g. navigating to another tab and back).
// The backend already keeps a durable ChatMessage history per session_id
// (GET /askdata/sessions/{id}/messages); persist just the session pointer
// here (sessionStorage — tab-scoped, cleared on tab close, which is the
// right lifetime for "resume where I left off," not a cross-device
// history feature) and rehydrate `turns` from that real backend data on
// mount instead of caching the rich UI state client-side.
const SESSION_STORAGE_KEY = "dataone_askdata_session";

interface StoredAskSession {
  sessionId: string;
  connectionId: number;
}

function readStoredSession(): StoredAskSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredAskSession>;
    if (typeof parsed.sessionId === "string" && typeof parsed.connectionId === "number") {
      return { sessionId: parsed.sessionId, connectionId: parsed.connectionId };
    }
    return null;
  } catch {
    return null;
  }
}

function writeStoredSession(session: StoredAskSession | null) {
  try {
    if (session) window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    else window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // sessionStorage may be unavailable (private mode) — persistence is a
    // nicety, never a blocker.
  }
}

function greetingTurn(): ChatTurn {
  return {
    role: "assistant",
    content: "Hi! I'm AskData — ask a question in plain English and I'll ground it in your schema, generate SQL, and run it read-only.",
    timestamp: new Date().toLocaleTimeString(),
  };
}

function turnFromHistory(m: SessionMessageEntry): ChatTurn {
  const timestamp = new Date(m.created_at).toLocaleTimeString();
  if (m.role !== "assistant") {
    return { role: "user", content: m.content, timestamp };
  }
  // Only sql_text/row_count were persisted, not the full result payload —
  // reconstruct enough for "Show SQL" / "Edit in SQL" to keep working, but
  // leave `executed` false so ChatBubble never renders a result table for
  // rows that were never stored (would otherwise look like "0 rows").
  const response: AskDataAskResponse | undefined = m.sql_text
    ? {
        session_id: "", sql: m.sql_text, grounded: false, confidence: 0, method: "restored",
        executed: false, columns: [], rows: [], row_count: m.row_count ?? 0,
        masked_columns: [], summary: null, warnings: [], error: null,
      }
    : undefined;
  return { role: "assistant", content: m.content, timestamp, response };
}

interface AskDataViewProps {
  connections: Connection[];
  connectionId: number | null;
  setConnectionId: (id: number) => void;
  /** Optional pre-filled question (from a handoff) — NOT auto-sent. */
  prefillQuestion?: string;
  /** Callback to edit SQL in the SQL view */
  onEditInSql: (connectionId: number, sql: string) => void;
  /** Called when `loading` transitions from true to false (background completion). */
  onBackgroundComplete?: () => void;
}

export default function AskDataView({
  connections,
  connectionId,
  setConnectionId,
  prefillQuestion,
  onEditInSql,
  onBackgroundComplete,
}: AskDataViewProps) {
  const [turns, setTurns] = useState<ChatTurn[]>([greetingTurn()]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreAttemptedRef = useRef(false);

  // Schema state
  const [tables, setTables] = useState<any[]>([]);
  const [srcCat, setSrcCat] = useState("");
  const [srcSch, setSrcSch] = useState("");
  const [srcTbl, setSrcTbl] = useState("");

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  // Load schema
  useEffect(() => {
    if (!connectionId) return;
    setTables([]);
    setSrcCat("");
    setSrcSch("");
    setSrcTbl("");
    api.get<{ schema: Record<string, any[]> }>(`/api/v1/connectors/${connectionId}/schema`)
      .then((res) => {
        let uid = 1;
        setTables(Object.entries(res.schema ?? {}).map(([name, cols]) => ({
          id: uid++, table_name: name,
        })));
      })
      .catch(() => {});
  }, [connectionId]);

  const struct = useMemo(() => {
    const s: Record<string, Record<string, string[]>> = {};
    tables.forEach(t => {
      const p = t.table_name.split(".");
      const [cat, sch, tbl] = p.length >= 3 ? [p[0], p[1], p.slice(2).join(".")] : p.length === 2 ? ["default", p[0], p[1]] : ["default", "default", t.table_name];
      if (!s[cat]) s[cat] = {};
      if (!s[cat][sch]) s[cat][sch] = [];
      s[cat][sch].push(tbl);
    });
    return s;
  }, [tables]);

  const cats = Object.keys(struct).sort();
  const srcSchs = srcCat ? Object.keys(struct[srcCat] || {}).sort() : [];
  const srcTbls = srcSch ? (struct[srcCat]?.[srcSch] || []).sort() : [];

  // Restore the last conversation in this browser tab (B05): this
  // component remounts fresh whenever the user navigates to a different
  // route and back to Query Workspace, which previously discarded `turns`
  // and `sessionId` even though the backend already persists the full
  // exchange. Runs once `connectionId` is known; a stored session for a
  // *different* connection is intentionally left alone (the backend
  // itself refuses to mix connections in one session).
  useEffect(() => {
    if (restoreAttemptedRef.current || connectionId == null) return;
    restoreAttemptedRef.current = true;
    const stored = readStoredSession();
    if (!stored || stored.connectionId !== connectionId) return;
    api
      .get<SessionMessagesResponse>(`/api/v1/askdata/sessions/${stored.sessionId}/messages`)
      .then((data) => {
        if (data.messages.length === 0) return;
        setTurns([greetingTurn(), ...data.messages.map(turnFromHistory)]);
        setSessionId(stored.sessionId);
      })
      .catch(() => {
        // A stale/expired session id just starts fresh — restoring is a
        // nicety, never a blocker.
        writeStoredSession(null);
      });
  }, [connectionId]);

  // Apply prefillQuestion once on mount (or when it changes via handoff)
  useEffect(() => {
    if (prefillQuestion) {
      setInput(prefillQuestion);
    }
  }, [prefillQuestion]);

  // Track loading → false transitions for the background-completion badge
  // (mirrors SqlWorkspaceView's running → false tracking).
  const prevLoadingRef = useRef(loading);
  useEffect(() => {
    if (prevLoadingRef.current && !loading) {
      onBackgroundComplete?.();
    }
    prevLoadingRef.current = loading;
  }, [loading, onBackgroundComplete]);

  const sendMessage = async (text?: string) => {
    const question = text ?? input;
    if (!question.trim() || connectionId == null) return;
    setTurns((p) => [...p, { role: "user", content: question, timestamp: new Date().toLocaleTimeString() }]);
    setInput("");
    setLoading(true);
    try {
      const payload: any = {
        connection_id: connectionId,
        question,
        session_id: sessionId,
      };
      if (srcCat && srcSch && srcTbl) {
        payload.table_name = `${srcCat}.${srcSch}.${srcTbl}`;
      }
      
      const data = await api.post<AskDataAskResponse>("/api/v1/askdata/ask", payload);
      setSessionId(data.session_id);
      writeStoredSession({ sessionId: data.session_id, connectionId });
      setTurns((p) => [...p, {
        role: "assistant",
        content: data.error ?? data.summary ?? "No response generated.",
        timestamp: new Date().toLocaleTimeString(),
        response: data,
      }]);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "AskData is unreachable — is the API running?";
      setTurns((p) => [...p, { role: "assistant", content: message, timestamp: new Date().toLocaleTimeString() }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const startNewChat = () => {
    writeStoredSession(null);
    setSessionId(undefined);
    setTurns([greetingTurn()]);
    setInput("");
    inputRef.current?.focus();
  };

  const isInitialState = turns.length <= 1;

  return (
    <div className="flex h-full flex-col bg-[#050505] p-2 sm:p-4 text-fg relative">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-white/[0.03] via-transparent to-transparent pointer-events-none" />

      {/* CURVED CHAT WINDOW */}
      <div className="flex-1 flex flex-col bg-background rounded-t-[2.5rem] rounded-b-3xl border border-white/[0.05] shadow-[0_0_40px_rgba(0,0,0,0.5)] overflow-hidden relative isolate">
        
        {/* Animated Glow Effect */}
        <div className="absolute inset-0 pointer-events-none shadow-[inset_0_0_60px_rgba(255,255,255,0.02)] animate-pulse" style={{ animationDuration: '4s' }} />

        {/* INTEGRATED HEADER */}
        <div className="flex items-center justify-between gap-4 border-b border-border/40 bg-background/60 backdrop-blur-xl px-8 py-5 z-10 relative">
          <div className="flex items-center gap-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/[0.08] bg-gradient-to-b from-white/5 to-transparent shadow-sm">
              <svg className="w-5 h-5 text-fg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
            </div>
            <div>
              <h2 className="text-[16px] font-semibold text-fg tracking-tight">AskData</h2>
              <p className="text-[12px] text-fg-subtle font-medium mt-0.5">Conversational database intelligence</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 bg-surface-overlay/50 p-1.5 rounded-xl border border-border/50 backdrop-blur-md">
              <ConnectionPicker connections={connections} value={connectionId} onChange={setConnectionId} />
            
              {/* Catalog Picker */}
              <select
                value={srcCat}
                onChange={(e) => { setSrcCat(e.target.value); setSrcSch(""); setSrcTbl(""); }}
                className="bg-transparent hover:bg-surface rounded-lg px-3 py-1.5 text-[12px] text-fg focus:outline-none transition-colors appearance-none cursor-pointer"
                title="Catalog"
              >
                <option value="">Catalog…</option>
                {cats.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              {/* Schema Picker */}
              <select
                value={srcSch}
                onChange={(e) => { setSrcSch(e.target.value); setSrcTbl(""); }}
                disabled={!srcCat}
                className="bg-transparent hover:bg-surface rounded-lg px-3 py-1.5 text-[12px] text-fg focus:outline-none transition-colors disabled:opacity-30 appearance-none cursor-pointer"
                title="Schema"
              >
                <option value="">Schema…</option>
                {srcSchs.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              {/* Table Picker */}
              <select
                value={srcTbl}
                onChange={(e) => setSrcTbl(e.target.value)}
                disabled={!srcSch}
                className="bg-transparent hover:bg-surface rounded-lg px-3 py-1.5 text-[12px] text-fg focus:outline-none transition-colors disabled:opacity-30 appearance-none cursor-pointer"
                title="Table"
              >
                <option value="">Table (Optional)…</option>
                {srcTbls.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {sessionId && (
              <button
                onClick={startNewChat}
                className="flex items-center justify-center w-9 h-9 rounded-xl border border-border/50 bg-surface-overlay/50 hover:bg-surface transition-colors text-fg-subtle hover:text-fg backdrop-blur-md"
                title="New Chat"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
              </button>
            )}
          </div>
        </div>

      {/* CHAT AREA */}
      {!isInitialState && (
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-6 relative bg-background">
          {turns.slice(1).map((turn, i) => (
            <div key={i}>
              <ChatBubble turn={turn} connectionId={connectionId} onEditInSql={onEditInSql} />
              {turn.response?.plan_id != null && (
                <div className="flex justify-start mb-6 animate-in fade-in duration-300">
                  <div className="max-w-[85%] w-full">
                    <SchemaDesignPlanCard planId={turn.response.plan_id} />
                  </div>
                </div>
              )}
            </div>
          ))}
          {loading && (
            <div className="flex justify-start mb-6 animate-in fade-in duration-300">
              <div className="bg-surface-overlay border border-border rounded-xl px-5 py-3 flex items-center gap-3 shadow-sm">
                <div className="flex gap-1.5">
                  <div className="w-1.5 h-1.5 bg-fg rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <div className="w-1.5 h-1.5 bg-fg rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <div className="w-1.5 h-1.5 bg-fg rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
                <span className="text-[12px] font-medium text-fg-subtle tracking-wide">Analyzing data...</span>
              </div>
            </div>
          )}
          <div ref={endRef} className="h-4" />
        </div>
      )}

      {/* INITIAL STATE / INPUT BAR WRAPPER */}
      <div className={`flex w-full transition-all duration-700 ease-in-out ${isInitialState ? 'flex-1 items-center justify-center' : 'p-6 pt-0 bg-background'}`}>
        <div className={`w-full ${isInitialState ? 'max-w-2xl px-6' : 'max-w-full'}`}>
          {isInitialState && (
            <div className="text-center mb-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
              <h1 className="text-2xl font-bold text-fg mb-2 tracking-tight">What do you want to know?</h1>
              <p className="text-sm text-fg-subtle">Ask a read-only question in plain English, and AskData will query the database for you.</p>
            </div>
          )}
          <div className="relative flex items-center bg-surface border border-border rounded-xl p-1.5 shadow-sm transition-all focus-within:border-fg/40 focus-within:ring-1 focus-within:ring-fg/20">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
              placeholder="E.g., what is the avg sales rate this quarter?"
              aria-label="Ask a question about your data"
              className="flex-1 bg-transparent px-4 py-3 text-[14px] text-fg placeholder:text-fg-subtle focus:outline-none"
            />
            <button
              onClick={() => sendMessage()}
              disabled={loading || !input.trim() || connectionId == null}
              className="shrink-0 flex items-center justify-center w-10 h-10 rounded-lg bg-fg text-background hover:opacity-90 transition-all disabled:opacity-30 disabled:hover:opacity-30"
              title="Ask"
            >
              {loading ? (
                <div className="w-4 h-4 border-2 border-background border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 12h14M12 5l7 7-7 7" /></svg>
              )}
            </button>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
