"use client";
import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql, PostgreSQL, MySQL, SQLite, PLSQL, StandardSQL } from "@codemirror/lang-sql";
import { EditorView, keymap } from "@codemirror/view";
import type { KeyBinding } from "@codemirror/view";
import { CatalogTable } from "../lib/types";

const DIALECTS: Record<string, typeof StandardSQL> = {
  postgres: PostgreSQL,
  mysql: MySQL,
  sqlite: SQLite,
  oracle: PLSQL,
  databricks: StandardSQL, // Databricks uses ANSI SQL with extensions
};

export default function SqlEditor({
  value,
  onChange,
  connectionType,
  tables,
  onRun,
}: {
  value: string;
  onChange: (sql: string) => void;
  connectionType?: string;
  tables: CatalogTable[];
  onRun: () => void;
}) {
  const schema = useMemo(() => {
    const s: Record<string, string[]> = {};
    for (const t of tables) {
      s[t.table_name] = t.columns.map((c) => c.column_name);
    }
    return s;
  }, [tables]);

  const dialect = (connectionType && DIALECTS[connectionType]) || StandardSQL;

  const runKeymap: KeyBinding[] = useMemo(
    () => [
      { key: "Mod-Enter", run: () => { onRun(); return true; } },
    ],
    [onRun],
  );

  const extensions = useMemo(
    () => [sql({ dialect, schema, upperCaseKeywords: true }), keymap.of(runKeymap), EditorView.lineWrapping],
    [dialect, schema, runKeymap],
  );

  return (
    <CodeMirror
      value={value}
      height="200px"
      theme="dark"
      extensions={extensions}
      onChange={onChange}
      basicSetup={{ lineNumbers: true, foldGutter: false }}
    />
  );
}
