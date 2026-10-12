import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DriftHistoryPanel from "../DriftHistoryPanel";
import type { DriftHistoryResponse } from "../../lib/types";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function historyWithDrift(): DriftHistoryResponse {
  return {
    connection: "widgets-db",
    snapshots: [{
      id: 1,
      schema_hash: "abc",
      captured_at: "2026-07-14T00:00:00Z",
      table_count: 3,
      drift_event: {
        id: 1,
        tables_added: [],
        tables_removed: [],
        columns_added: { customers: ["email"] },
        columns_removed: {},
        type_changes: {},
        detected_at: "2026-07-14T00:00:00Z",
      },
    }],
  };
}

describe("DriftHistoryPanel", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("writes a WorkspaceHandoff and navigates when Investigate is clicked on a drifted table", () => {
    render(
      <DriftHistoryPanel
        history={historyWithDrift()}
        onRescan={vi.fn()}
        role="admin"
        connectionId={7}
      />
    );

    fireEvent.click(screen.getByText("Investigate customers →"));

    const raw = sessionStorage.getItem("query-workspace-handoff");
    expect(raw).not.toBeNull();
    const payload = JSON.parse(raw as string);
    expect(payload).toMatchObject({
      connectionId: 7,
      mode: "sql",
      sql: "SELECT * FROM customers LIMIT 100;",
      banner: { sourceModule: "schema_intel" },
    });
    expect(pushMock).toHaveBeenCalledWith("/dashboard/query-workspace");
  });

  it("does not render an Investigate action for a table that was only removed", () => {
    const history: DriftHistoryResponse = {
      connection: "widgets-db",
      snapshots: [{
        id: 1,
        schema_hash: "abc",
        captured_at: "2026-07-14T00:00:00Z",
        table_count: 2,
        drift_event: {
          id: 1,
          tables_added: [],
          tables_removed: ["archived_orders"],
          columns_added: {},
          columns_removed: {},
          type_changes: {},
          detected_at: "2026-07-14T00:00:00Z",
        },
      }],
    };
    render(<DriftHistoryPanel history={history} onRescan={vi.fn()} role="admin" connectionId={7} />);
    expect(screen.queryByText(/Investigate archived_orders/)).toBeNull();
  });

  it("no-ops when connectionId is not yet known", () => {
    render(
      <DriftHistoryPanel history={historyWithDrift()} onRescan={vi.fn()} role="admin" connectionId={null} />
    );
    fireEvent.click(screen.getByText("Investigate customers →"));
    expect(sessionStorage.getItem("query-workspace-handoff")).toBeNull();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
