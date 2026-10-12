import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readAndClearWorkspaceHandoff, writeWorkspaceHandoff } from "../lib/handoff";

describe("handoff.ts", () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it("round-trips a payload and clears it after one read", () => {
    writeWorkspaceHandoff({
      connectionId: 1,
      mode: "sql",
      sql: "SELECT * FROM widgets LIMIT 100;",
      banner: { sourceModule: "schema_intel", summary: "Drift on widgets" },
    });

    const first = readAndClearWorkspaceHandoff();
    expect(first).toEqual({
      connectionId: 1,
      mode: "sql",
      sql: "SELECT * FROM widgets LIMIT 100;",
      banner: { sourceModule: "schema_intel", summary: "Drift on widgets" },
    });

    // Second read must return null — the key is removed after the first read.
    expect(readAndClearWorkspaceHandoff()).toBeNull();
  });

  it("returns null when nothing has been written", () => {
    expect(readAndClearWorkspaceHandoff()).toBeNull();
  });

  it("returns null and does not throw on malformed JSON", () => {
    sessionStorage.setItem("query-workspace-handoff", "{not json");
    expect(readAndClearWorkspaceHandoff()).toBeNull();
  });
});
