import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceSetupPage from "../workspace-setup/page";
import type { InstallStatus } from "../types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock, post: postMock, base: "http://api" } }));

function status(over: Partial<InstallStatus> = {}): InstallStatus {
  return {
    workspace_host: "https://dbc-1.cloud.databricks.com", signed_in_with_databricks: true, ingest_key_configured: true,
    current_release: "1.0.0", installed: null, up_to_date: false, ...over,
  };
}

function route(st: InstallStatus) {
  getMock.mockImplementation(async (path: string) =>
    path.endsWith("/status") ? st : { catalogs: ["main", "workspace"] });
}

beforeEach(() => { getMock.mockReset(); postMock.mockReset(); });

describe("WorkspaceSetupPage", () => {
  it("asks the user to sign in with Databricks when they have not", async () => {
    route(status({ signed_in_with_databricks: false }));
    render(<WorkspaceSetupPage />);
    const link = await screen.findByRole("link", { name: "Sign in with Databricks" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/api/v1/auth/databricks/login?redirect_to="));
    expect(screen.getByRole("button", { name: "Install in my workspace" })).toBeDisabled();
  });

  it("warns when the server cannot receive results and blocks install", async () => {
    route(status({ ingest_key_configured: false }));
    render(<WorkspaceSetupPage />);
    expect(await screen.findByText(/no DQ_INGEST_KEY configured/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install in my workspace" })).toBeDisabled();
  });

  it("installs with the chosen catalog, prefix and schedule, then lists each step", async () => {
    route(status());
    postMock.mockResolvedValue({
      ok: true, release: "1.0.0", job_id: 7, notebook_folder: "/x",
      steps: [{ step: "schema", status: "created", detail: "workspace.acme_bronze" }, { step: "job", status: "created", detail: "DataOne Data Quality (id 7)" }],
    });
    render(<WorkspaceSetupPage />);
    await waitFor(() => expect(screen.getByLabelText("Catalog")).toHaveValue("workspace"));
    fireEvent.change(screen.getByLabelText(/Schema prefix/), { target: { value: "acme" } });
    fireEvent.change(screen.getByLabelText("Data quality schedule"), { target: { value: "0 0 6 * * ?" } });
    fireEvent.click(screen.getByRole("button", { name: "Install in my workspace" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/api/v1/databricks/install", {
      catalog: "workspace", schema_prefix: "acme", schedule_cron: "0 0 6 * * ?",
    }));
    expect(await screen.findByText("Installed release 1.0.0")).toBeInTheDocument();
    expect(screen.getByText("workspace.acme_bronze")).toBeInTheDocument();
  });

  it("shows where a failed install stopped", async () => {
    route(status());
    postMock.mockResolvedValue({ ok: false, release: "1.0.0", job_id: null, notebook_folder: null,
      steps: [{ step: "volume", status: "failed", detail: "User cannot CREATE VOLUME" }] });
    render(<WorkspaceSetupPage />);
    await waitFor(() => expect(screen.getByLabelText("Catalog")).toHaveValue("workspace"));
    fireEvent.click(screen.getByRole("button", { name: "Install in my workspace" }));
    expect(await screen.findByText(/stopped at the first failed step/)).toBeInTheDocument();
    expect(screen.getByText("User cannot CREATE VOLUME")).toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    route(status());
    postMock.mockRejectedValue(new Error("Your Databricks session expired; sign in with Databricks again"));
    render(<WorkspaceSetupPage />);
    await waitFor(() => expect(screen.getByLabelText("Catalog")).toHaveValue("workspace"));
    fireEvent.click(screen.getByRole("button", { name: "Install in my workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("session expired");
  });

  it("offers an update and a run button once installed", async () => {
    route(status({ up_to_date: false, installed: { catalog: "main", schema_prefix: "acme", job_id: 7, release: "0.9.0",
      installed_by: "me@corp.com", installed_at: "2026-10-01T00:00:00Z", notebook_folder: "/x" } }));
    postMock.mockResolvedValue({ run_id: 5, run_url: "https://dbc-1/jobs/7/runs/5" });
    render(<WorkspaceSetupPage />);
    expect(await screen.findByText(/Update available \(1.0.0\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update installation" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Run data quality now" }));
    expect(await screen.findByRole("link", { name: /view the run/ })).toHaveAttribute("href", "https://dbc-1/jobs/7/runs/5");
  });
});
