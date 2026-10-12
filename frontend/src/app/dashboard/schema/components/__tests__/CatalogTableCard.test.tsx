import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CatalogTableCard from "../CatalogTableCard";
import type { CatalogTable } from "../../lib/types";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function tableWith(classification: CatalogTable["columns"][number]["classification"]): CatalogTable {
  return {
    id: 1,
    connection_id: 42,
    table_name: "customers",
    last_scanned_at: "2026-07-14T00:00:00Z",
    columns: [{
      id: 1,
      column_name: "email",
      data_type: "varchar",
      nullable: true,
      is_primary_key: false,
      ordinal_position: 1,
      foreign_keys: [],
      profile: null,
      classification,
    }],
  };
}

describe("CatalogTableCard", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows Investigate for a PII column and writes an Ask-mode handoff naming the exact column", () => {
    const table = tableWith({
      label: "PII", level: "High", confidence: 0.92, method: "keyword",
      overridden_by: null, overridden_at: null, classified_at: "2026-07-14T00:00:00Z",
    });
    render(<CatalogTableCard table={table} role="admin" onOverride={vi.fn()} />);

    fireEvent.click(screen.getByText("Investigate →"));

    const raw = sessionStorage.getItem("query-workspace-handoff");
    expect(raw).not.toBeNull();
    const payload = JSON.parse(raw as string);
    expect(payload.connectionId).toBe(42);
    expect(payload.mode).toBe("ask");
    expect(payload.prefillQuestion).toContain("customers.email");
    expect(payload.banner.sourceModule).toBe("schema_intel");
    expect(pushMock).toHaveBeenCalledWith("/dashboard/query-workspace");
  });

  it("does not show Investigate for a Public column", () => {
    const table = tableWith({
      label: "Public", level: "Low", confidence: 0.5, method: "keyword",
      overridden_by: null, overridden_at: null, classified_at: "2026-07-14T00:00:00Z",
    });
    render(<CatalogTableCard table={table} role="admin" onOverride={vi.fn()} />);
    expect(screen.queryByText("Investigate →")).toBeNull();
  });
});
