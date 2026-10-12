import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReportButtons from "../ReportButtons";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  ApiError: class ApiError extends Error {},
}));

describe("ReportButtons (Enterprise v2, E15)", () => {
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    getMock.mockReset();
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it("fetches and downloads the documentation artifact", async () => {
    getMock.mockResolvedValue({ content: "# Doc", filename: "mapping-1-documentation.md" });
    render(<ReportButtons mappingId={1} />);

    fireEvent.click(screen.getByRole("button", { name: "Generate documentation" }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/api/v1/mappings/1/documentation"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
  });

  it("fetches and downloads the migration report artifact", async () => {
    getMock.mockResolvedValue({ content: "# Report", filename: "mapping-1-migration-report.md" });
    render(<ReportButtons mappingId={1} />);

    fireEvent.click(screen.getByRole("button", { name: "Generate migration report" }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/api/v1/mappings/1/migration-report"));
  });

  it("shows an error message when generation fails, without crashing", async () => {
    getMock.mockRejectedValue(new Error("report backend unreachable"));
    render(<ReportButtons mappingId={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate documentation" }));
    await waitFor(() => expect(screen.getByText("Report generation failed.")).toBeInTheDocument());
  });

  it("disables both buttons while a report is generating", async () => {
    let resolveFetch!: (v: unknown) => void;
    getMock.mockImplementation(() => new Promise((r) => (resolveFetch = r)));
    render(<ReportButtons mappingId={1} />);

    fireEvent.click(screen.getByRole("button", { name: "Generate documentation" }));
    expect(screen.getByRole("button", { name: "Generate migration report" })).toBeDisabled();

    resolveFetch({ content: "# Doc", filename: "f.md" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate migration report" })).not.toBeDisabled());
  });
});
