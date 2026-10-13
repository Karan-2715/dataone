import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GovernanceCenterPage from "../page";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock, put: putMock } }));

const defaultAssets = [
  { table_name: "users", owner: null, steward: null, classification: "Sensitive", classification_source: "automatic", retention_policy: null, compliance_status: "not_assessed" },
  { table_name: "orders", owner: null, steward: null, classification: "Confidential", classification_source: "automatic", retention_policy: null, compliance_status: "not_assessed" },
];

function mockRoutes({ assets = defaultAssets as unknown[] } = {}) {
  getMock.mockImplementation((path: string) => {
    if (path.includes("/governance/score")) {
      return Promise.resolve({ score: 66, table_count: 2, owner_coverage: 50, classification_coverage: 100, retention_coverage: 50 });
    }
    if (path.includes("/connectors/")) return Promise.resolve([{ id: 1, name: "Prod DB" }]);
    if (path.includes("/governance/1/assets")) return Promise.resolve(assets);
    return Promise.resolve(null);
  });
}

describe("GovernanceCenterPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("shows the coverage gauge and prompts for a connection", async () => {
    mockRoutes();
    render(<GovernanceCenterPage />);
    await waitFor(() => expect(screen.getByText("Pick a connection")).toBeInTheDocument());
    expect(screen.getByText("Owner 50%")).toBeInTheDocument();
  });

  it("lists cataloged tables with '—' for ungoverned fields, not fabricated values", async () => {
    mockRoutes();
    render(<GovernanceCenterPage />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Connection" }), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByText("users")).toBeInTheDocument());
    await waitFor(() => expect(getMock).toHaveBeenCalledWith(
      "/api/v1/governance/score?connection_id=1",
      expect.anything(),
    ));
    expect(screen.getByText("Selected connection")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows existing governance values from the backend record", async () => {
    mockRoutes({
      assets: [{
        table_name: "users", owner: "Data Team", steward: "Jane",
        classification: "Classified", classification_source: "manual", retention_policy: "3 years", compliance_status: "compliant",
      }],
    });
    render(<GovernanceCenterPage />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Connection" }), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByText("Data Team")).toBeInTheDocument());
    expect(screen.getByText("Classified")).toBeInTheDocument();
    expect(screen.getByText("compliant")).toBeInTheDocument();
  });

  it("edits and saves governance metadata for a table", async () => {
    mockRoutes();
    putMock.mockResolvedValue({});
    render(<GovernanceCenterPage />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Connection" }), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByText("users")).toBeInTheDocument());

    fireEvent.click(screen.getAllByText("Edit")[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Owner" }), { target: { value: "New Owner" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith(
      "/api/v1/governance/1",
      expect.objectContaining({ table_name: "users", owner: "New Owner" }),
    ));
  });

  it("shows a save error without crashing", async () => {
    mockRoutes();
    putMock.mockRejectedValue(new Error("classification must be one of the allowed set"));
    render(<GovernanceCenterPage />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Connection" }), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByText("users")).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("Edit")[0]);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("classification must be one of the allowed set")).toBeInTheDocument());
  });
});
