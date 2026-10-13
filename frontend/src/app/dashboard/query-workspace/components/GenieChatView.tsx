"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Connection } from "../../query-studio/lib/types";

// ── API shapes (backend/app/api/routers/genie_chat.py) ───────────────────────
export interface GenieSpace { space_id: string; title: string | null; description: string | null; auto?: boolean }
interface SpacesResponse { spaces: GenieSpace[]; configured: boolean; default_space_id: string | null }
interface StartResponse { space_id: string; conversation_id: string; message_id: string }
interface DataSpaceResponse { space_id: string; title: string; created: boolean; tables: string[] }
export interface GenieResult {
  status: string;
  done: boolean;
  answer: string | null;
  sql: string | null;
  sql_title: string | null;
  sql_description: string | null;
  columns: { name: string; type: string }[];
  rows: (string | null)[][];
  row_count: number | null;
  truncated: boolean;
  suggested_questions: string[];
  error: string | null;
}
interface Turn { role: "user" | "assistant"; content: string; result?: GenieResult }
interface Scope { catalog: string; schema: string; table: string }

const POLL_MS = 2_000;
const MAX_POLLS = 150; // ~5 minutes — Genie answers in 10-120s
const SPACE_KEY = "dataone.genie.space";
const VISIBLE_ROWS = 50;
const NO_SCOPE: Scope = { catalog: "", schema: "", table: "" };

const DATAONE_STARTERS = [
  "What is the latest quality score for each DataOne project?",
  "Which columns have the lowest quality scores in the latest run?",
  "Which cleaning transformations changed the most cells?",
];
const GENERIC_STARTERS = ["What can I ask about this data?", "Give me a summary of the main tables"];

function statusLabel(status: string, scopeLabel: string): string {
  switch (status) {
    case "PREPARING": return `Preparing Genie for ${scopeLabel}…`;
    case "ASKING_AI": return "Genie is writing SQL…";
    case "EXECUTING_QUERY":
    case "PENDING_WAREHOUSE": return "Running the query…";
    case "FETCHING_METADATA": return "Reading table metadata…";
    default: return "Understanding your question…";
  }
}

function describe(err: unknown): string {
  return err instanceof ApiError || err instanceof Error ? err.message : "Request failed";
}

function readSpace(): string | null {
  try { return window.localStorage.getItem(SPACE_KEY); } catch { return null; }
}

const scopeKey = (s: Scope) => [s.catalog, s.schema, s.table].filter(Boolean).join(".");

interface Props {
  connections: Connection[];
  /** The workspace's shared connection — used as the starting Databricks connection when it is one. */
  connectionId: number | null;
  /** Hands generated SQL (and this chat's Databricks connection) to the SQL editor. */
  onEditInSql: (connectionId: number, sql: string) => void;
  /** Pre-filled question (from a Schema Intel / Schema Mapper handoff) — NOT auto-sent. */
  prefillQuestion?: string;
  onBackgroundComplete?: () => void;
}

export default function GenieChatView({
  connections, connectionId: sharedConnectionId, onEditInSql, prefillQuestion, onBackgroundComplete,
}: Props) {
  const databricksConnections = useMemo(() => connections.filter((c) => c.type?.toLowerCase() === "databricks"), [connections]);
  // The chat's own Databricks connection: choosing one here never changes the SQL editor's connection.
  const [chosenConnectionId, setChosenConnectionId] = useState<number | null>(null);
  const connectionId = databricksConnections.some((c) => c.id === chosenConnectionId)
    ? chosenConnectionId
    : databricksConnections.find((c) => c.id === sharedConnectionId)?.id ?? databricksConnections[0]?.id ?? null;

  // ── Genie agents (curated ones; per-selection agents are created behind the scenes) ──
  const [spaces, setSpaces] = useState<GenieSpace[]>([]);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);

  // ── Catalog / schema / table scope ──
  const [tableNames, setTableNames] = useState<string[]>([]); // "catalog.schema.table" from the Databricks connection
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [scope, setScope] = useState<Scope>(NO_SCOPE);
  const scopeSpaces = useRef<Record<string, string>>({}); // scope key -> Genie agent id

  // ── Conversation ──
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const completeRef = useRef(onBackgroundComplete);
  completeRef.current = onBackgroundComplete;

  useEffect(() => () => { mounted.current = false; }, []);
  useEffect(() => { endRef.current?.scrollIntoView?.({ behavior: "smooth" }); }, [turns, phase]);
  useEffect(() => { if (prefillQuestion) setInput(prefillQuestion); }, [prefillQuestion]);

  // Agents
  useEffect(() => {
    let cancelled = false;
    api.get<SpacesResponse>("/api/v1/genie/spaces")
      .then((res) => {
        if (cancelled) return;
        const curated = res.spaces.filter((s) => !s.auto);
        setSpaces(curated);
        const stored = readSpace();
        const pick = curated.find((s) => s.space_id === stored)?.space_id
          ?? curated.find((s) => s.space_id === res.default_space_id)?.space_id ?? curated[0]?.space_id ?? null;
        setSpaceId(pick);
        if (!pick) setSetupError("No Genie agent is available to this app yet — pick a catalog, schema and table below to create one.");
      })
      .catch((err) => { if (!cancelled) setSetupError(describe(err)); });
    return () => { cancelled = true; };
  }, []);

  // Catalogs / schemas / tables of the selected connection
  const loadCatalogs = useCallback(async (refresh = false) => {
    if (connectionId == null) return;
    setSchemaLoading(true); setSchemaError(null);
    try {
      const res = await api.get<{ schema: Record<string, unknown> }>(`/api/v1/connectors/${connectionId}/schema${refresh ? "?refresh=true" : ""}`);
      if (!mounted.current) return;
      setTableNames(Object.keys(res.schema ?? {}));
      setScope(NO_SCOPE);
    } catch (err) {
      if (mounted.current) setSchemaError(`Could not load catalogs: ${describe(err)}`);
    } finally {
      if (mounted.current) setSchemaLoading(false);
    }
  }, [connectionId]);

  useEffect(() => { setTableNames([]); void loadCatalogs(); }, [loadCatalogs]);

  const tree = useMemo(() => {
    const t: Record<string, Record<string, string[]>> = {};
    for (const full of tableNames) {
      const [cat, sch, ...rest] = full.split(".");
      const tbl = rest.join(".");
      if (!cat || !sch || !tbl) continue;
      t[cat] ??= {};
      t[cat][sch] ??= [];
      if (tbl !== "(empty_schema)") t[cat][sch].push(tbl);
    }
    return t;
  }, [tableNames]);
  const catalogs = Object.keys(tree).sort();
  const schemas = scope.catalog ? Object.keys(tree[scope.catalog] ?? {}).sort() : [];
  const tables = scope.catalog && scope.schema ? [...(tree[scope.catalog]?.[scope.schema] ?? [])].sort() : [];

  const scoped = Boolean(scope.catalog && scope.schema);
  const incompleteScope = Boolean(scope.catalog) && !scope.schema;
  const label = scopeKey(scope);
  const resetConversation = () => { setConversationId(null); setTurns([]); setPhase(null); };

  const changeScope = (next: Scope) => { setScope(next); resetConversation(); };
  const chooseSpace = (id: string) => {
    setSpaceId(id);
    resetConversation();
    try { window.localStorage.setItem(SPACE_KEY, id); } catch { /* storage unavailable */ }
  };

  const ask = useCallback(async (raw: string) => {
    const question = raw.trim();
    if (!question || phase !== null || incompleteScope) return;
    setInput("");
    setTurns((t) => [...t, { role: "user", content: question }]);
    const reply = (content: string, result?: GenieResult) =>
      mounted.current && setTurns((t) => [...t, { role: "assistant", content, result }]);
    try {
      // A selected catalog/schema/table gets (or reuses) a Genie agent that has exactly that data.
      let activeSpace = spaceId;
      if (scoped) {
        const key = scopeKey(scope);
        if (!scopeSpaces.current[key]) {
          setPhase("PREPARING");
          const ds = await api.post<DataSpaceResponse>("/api/v1/genie/data-space", {
            catalog: scope.catalog, schema: scope.schema, table: scope.table || null,
          });
          scopeSpaces.current[key] = ds.space_id;
        }
        activeSpace = scopeSpaces.current[key];
      }
      if (!activeSpace) throw new Error("No Genie agent is available.");

      setPhase("SUBMITTED");
      const started = await api.post<StartResponse>("/api/v1/genie/chat", {
        question, space_id: activeSpace, conversation_id: conversationId,
      });
      if (mounted.current) setConversationId(started.conversation_id);
      for (let i = 0; i < MAX_POLLS && mounted.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const res = await api.get<GenieResult>(
          `/api/v1/genie/chat/${started.conversation_id}/${started.message_id}?space_id=${encodeURIComponent(started.space_id)}`,
        );
        if (!res.done) { if (mounted.current) setPhase(res.status); continue; }
        reply(res.error && !res.answer && !res.sql ? res.error : res.answer ?? (res.sql ? "Here is what I ran:" : "Genie finished without an answer."), res);
        completeRef.current?.();
        return;
      }
      reply("Genie is taking longer than expected. Try again, or ask a narrower question.");
    } catch (err) {
      reply(`Sorry, I couldn't reach Genie: ${describe(err)}`);
    } finally {
      if (mounted.current) setPhase(null);
    }
  }, [phase, incompleteScope, scoped, scope, spaceId, conversationId]);

  const empty = turns.length === 0;
  const active = spaces.find((s) => s.space_id === spaceId);
  const starters = scoped
    ? [`What columns does ${scope.table || scope.schema} have?`, `How many rows are in ${scope.table ? scope.table : "each table"}?`, "Give me a summary of this data"]
    : active?.title === "DataOne AskData" ? DATAONE_STARTERS : GENERIC_STARTERS;
  const canAsk = scoped || (!incompleteScope && Boolean(spaceId));
  const selectClass = "bg-transparent hover:bg-surface rounded-lg px-3 py-1.5 text-[12px] text-fg focus:outline-none transition-colors appearance-none cursor-pointer disabled:opacity-30";

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Connection · Genie agent · Catalog · Schema · Table */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-6 py-3">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/50 bg-surface-overlay/50 p-1.5 backdrop-blur-md">
          <label className="flex items-center gap-2 pl-2 text-xs text-fg-subtle">
            <span className="hidden sm:inline">Connection</span>
            <select aria-label="Connection" value={connectionId ?? ""} disabled={phase !== null || databricksConnections.length === 0}
              onChange={(e) => setChosenConnectionId(Number(e.target.value))} className={selectClass}>
              {databricksConnections.length === 0 && <option value="">No Databricks connection</option>}
              {databricksConnections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs text-fg-subtle">
            <span className="hidden sm:inline">Genie agent</span>
            <select aria-label="Genie agent" value={scoped ? "" : spaceId ?? ""} disabled={scoped || spaces.length === 0 || phase !== null}
              onChange={(e) => chooseSpace(e.target.value)} className={selectClass}
              title={scoped ? "A Genie agent over the selected data is used instead" : undefined}>
              {scoped && <option value="">Auto: {label}</option>}
              {!scoped && spaces.length === 0 && <option value="">{setupError ? "Unavailable" : "Loading…"}</option>}
              {spaces.map((s) => <option key={s.space_id} value={s.space_id}>{s.title ?? s.space_id}</option>)}
            </select>
          </label>
          <select aria-label="Catalog" title="Catalog" value={scope.catalog} disabled={phase !== null || catalogs.length === 0}
            onChange={(e) => changeScope({ catalog: e.target.value, schema: "", table: "" })} className={selectClass}>
            <option value="">Catalog…</option>
            {catalogs.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select aria-label="Schema" title="Schema" value={scope.schema} disabled={phase !== null || !scope.catalog}
            onChange={(e) => changeScope({ ...scope, schema: e.target.value, table: "" })} className={selectClass}>
            <option value="">Schema…</option>
            {schemas.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select aria-label="Table" title="Table" value={scope.table} disabled={phase !== null || !scope.schema}
            onChange={(e) => changeScope({ ...scope, table: e.target.value })} className={selectClass}>
            <option value="">All tables in schema…</option>
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button onClick={() => void loadCatalogs(true)} disabled={phase !== null || schemaLoading || connectionId == null}
            title="Refresh managed, external, and federated Unity Catalog datasets"
            className="rounded-lg px-2 py-1.5 text-[12px] text-fg-subtle hover:bg-surface hover:text-fg disabled:opacity-30">
            {schemaLoading ? "Refreshing…" : "Refresh catalogs"}
          </button>
        </div>
        {scope.catalog && (
          <button onClick={() => changeScope(NO_SCOPE)} disabled={phase !== null}
            className="text-[12px] text-fg-subtle underline-offset-2 hover:text-fg hover:underline disabled:opacity-40">Clear selection</button>
        )}
        {conversationId && (
          <button onClick={resetConversation} disabled={phase !== null} title="Start a new conversation"
            className="ml-auto rounded-xl border border-border/50 bg-surface-overlay/50 px-3 py-1.5 text-[12px] text-fg-subtle hover:text-fg disabled:opacity-40">
            New chat
          </button>
        )}
      </div>

      {schemaError && <div role="alert" className="border-b border-danger/20 bg-danger/10 px-6 py-2 text-xs text-danger">{schemaError}</div>}
      {setupError && !scoped && <div role="alert" className="border-b border-danger/20 bg-danger/10 px-6 py-2 text-xs text-danger">{setupError}</div>}
      {incompleteScope && (
        <div role="status" className="border-b border-border bg-surface-overlay px-6 py-2 text-xs text-fg-subtle">
          Pick a schema (and optionally a table) in <b>{scope.catalog}</b> to ask about that data.
        </div>
      )}

      {/* Conversation */}
      <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
        {empty && (
          <div className="m-auto max-w-2xl text-center">
            <h1 className="mb-2 text-2xl font-bold tracking-tight text-fg">{scoped ? `Ask about ${label}` : "Ask Genie"}</h1>
            <p className="mb-6 text-sm text-fg-subtle">
              {scoped
                ? "Genie writes the SQL for your question over just this data, runs it, and explains the result."
                : "Ask in plain English, or pick a catalog, schema and table above to ask about specific data."}
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {starters.map((q) => (
                <button key={q} onClick={() => void ask(q)} disabled={!canAsk || phase !== null}
                  className="rounded-full border border-border bg-surface px-4 py-2 text-[12px] text-fg-subtle hover:text-fg disabled:opacity-40">{q}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => <GenieBubble key={i} turn={t} connectionId={connectionId} onEditInSql={onEditInSql} onFollowUp={(q) => void ask(q)} busy={phase !== null} />)}
        {phase !== null && (
          <div className="flex justify-start" role="status" aria-live="polite">
            <div className="flex items-center gap-3 rounded-xl border border-border bg-surface-overlay px-5 py-3 shadow-sm">
              <div className="flex gap-1.5">
                {[0, 150, 300].map((d) => <div key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-fg" style={{ animationDelay: `${d}ms` }} />)}
              </div>
              <span className="text-[12px] font-medium tracking-wide text-fg-subtle">{statusLabel(phase, label)}</span>
            </div>
          </div>
        )}
        <div ref={endRef} className="h-2" />
      </div>

      {/* Input */}
      <div className="p-6 pt-0">
        <div className="flex items-center rounded-xl border border-border bg-surface p-1.5 shadow-sm focus-within:border-fg/40 focus-within:ring-1 focus-within:ring-fg/20">
          <input
            value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void ask(input)}
            placeholder={conversationId ? "Ask a follow-up…" : scoped ? `Ask about ${label}…` : "E.g., which project has the lowest quality score?"}
            aria-label="Ask Genie a question about your data"
            className="flex-1 bg-transparent px-4 py-3 text-[14px] text-fg placeholder:text-fg-subtle focus:outline-none"
          />
          <button onClick={() => void ask(input)} disabled={phase !== null || !input.trim() || !canAsk} title="Ask"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-fg text-background transition-all hover:opacity-90 disabled:opacity-30">
            {phase !== null
              ? <div className="h-4 w-4 animate-spin rounded-full border-2 border-background border-t-transparent" />
              : <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 12h14M12 5l7 7-7 7" /></svg>}
          </button>
        </div>
      </div>
    </div>
  );
}

function GenieBubble({ turn, connectionId, onEditInSql, onFollowUp, busy }: {
  turn: Turn; connectionId: number | null; onEditInSql: (id: number, sql: string) => void; onFollowUp: (q: string) => void; busy: boolean;
}) {
  const [showSql, setShowSql] = useState(false);
  const [copied, setCopied] = useState(false);
  const res = turn.result;

  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl border border-border bg-surface-elevated px-5 py-4 text-[14px] leading-relaxed text-fg whitespace-pre-wrap">{turn.content}</div>
      </div>
    );
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(res?.sql ?? ""); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  const failed = Boolean(res?.error) && !res?.rows.length;

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[85%] rounded-2xl border border-border bg-surface px-5 py-4 text-fg">
        <div className="mb-3 flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-background">
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
          </div>
          <span className="text-[12px] font-semibold">Genie</span>
        </div>
        <div className={`text-[14px] leading-relaxed whitespace-pre-wrap ${failed ? "text-danger" : ""}`}>{turn.content}</div>
        {res?.error && res.answer && <div className="mt-2 text-[12px] text-danger">{res.error}</div>}

        {res?.sql && (
          <div className="mt-4">
            <div className="flex flex-wrap items-center gap-4">
              <button onClick={() => setShowSql((s) => !s)}
                className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle hover:text-fg">
                <svg className={`h-3 w-3 transition-transform ${showSql ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                {showSql ? "Hide SQL" : "View generated SQL"}
              </button>
              {showSql && <button onClick={() => void copy()} className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle hover:text-fg">{copied ? "Copied" : "Copy"}</button>}
              <button onClick={() => connectionId != null && onEditInSql(connectionId, res.sql!)} disabled={connectionId == null}
                title={connectionId == null ? "No SQL connection available" : "Open this SQL in the SQL editor"}
                className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle hover:text-fg disabled:opacity-40">Edit in SQL</button>
            </div>
            {showSql && (
              <>
                {res.sql_description && <p className="mt-3 text-[12px] text-fg-subtle">{res.sql_description}</p>}
                <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-xl border border-border bg-background p-4 font-mono text-[12px] text-fg-subtle">{res.sql}</pre>
              </>
            )}
          </div>
        )}

        {res && res.columns.length > 0 && (
          <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-background">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr className="border-b border-border bg-surface-elevated">
                  {res.columns.map((c) => <th key={c.name} className="px-3 py-2 text-left font-semibold text-fg-subtle">{c.name}</th>)}
                </tr>
              </thead>
              <tbody>
                {res.rows.slice(0, VISIBLE_ROWS).map((row, i) => (
                  <tr key={i} className="border-b border-border/50 transition-colors last:border-0 hover:bg-surface-elevated">
                    {row.map((cell, j) => <td key={j} className="px-3 py-2 font-mono text-fg">{cell ?? ""}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="border-t border-border bg-surface-elevated p-2 text-center text-[10px] font-semibold uppercase tracking-widest text-fg-subtle">
              {res.rows.length === 0
                ? "No rows returned"
                : `Showing ${Math.min(res.rows.length, VISIBLE_ROWS)} of ${(res.row_count ?? res.rows.length).toLocaleString()} rows${res.truncated ? " (truncated)" : ""}`}
            </div>
          </div>
        )}

        {res && res.suggested_questions.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {res.suggested_questions.map((q) => (
              <button key={q} onClick={() => onFollowUp(q)} disabled={busy}
                className="rounded-full border border-border bg-background px-3 py-1.5 text-[11px] text-fg-subtle hover:text-fg disabled:opacity-40">{q}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
