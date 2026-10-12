import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceHeader from "../WorkspaceHeader";
import type { Mapping } from "../../lib/types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock },
  ApiError: class ApiError extends Error {},
}));

function makeMapping(overrides: Partial<Mapping> = {}): Mapping {
  return {
    id: 1,
    name: "Retail E2E",
    source_id: 10,
    target_id: 20,
    status: "draft",
    review_stage: "draft",
    current_version_id: null,
    created_by: "admin@test.local",
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    edges: [],
    ...overrides,
  };
}

const noop = () => {};
const noopAsync = async () => {};

describe("WorkspaceHeader (Revise, Enterprise v2 E13-6)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("does not show Revise for a draft mapping", () => {
    render(
      <WorkspaceHeader
        mapping={makeMapping({ status: "draft" })}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={noopAsync}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={noop}
      />,
    );
    expect(screen.queryByRole("button", { name: "Revise mapping" })).not.toBeInTheDocument();
  });

  it("shows Revise for a published mapping when the role can edit", () => {
    render(
      <WorkspaceHeader
        mapping={makeMapping({ status: "published", current_version_id: 3 })}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={noopAsync}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={noop}
      />,
    );
    expect(screen.getByRole("button", { name: "Revise mapping" })).toBeInTheDocument();
  });

  it("hides Revise for a viewer even on a published mapping", () => {
    render(
      <WorkspaceHeader
        mapping={makeMapping({ status: "published", current_version_id: 3 })}
        role="viewer"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={noopAsync}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={noop}
      />,
    );
    expect(screen.queryByRole("button", { name: "Revise mapping" })).not.toBeInTheDocument();
  });

  it("calls onRevise and shows a busy state while it resolves", async () => {
    let resolveRevise: () => void = () => {};
    const onRevise = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRevise = resolve;
        }),
    );
    render(
      <WorkspaceHeader
        mapping={makeMapping({ status: "published", current_version_id: 3 })}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={onRevise}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={noop}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Revise mapping" }));
    expect(onRevise).toHaveBeenCalled();
    expect(await screen.findByText("Reopening…")).toBeInTheDocument();
    resolveRevise();
    await waitFor(() => expect(screen.getByText("↺ Revise")).toBeInTheDocument());
  });

  it("does not crash when onRevise rejects", async () => {
    const onRevise = vi.fn().mockRejectedValue(new Error("boom"));
    render(
      <WorkspaceHeader
        mapping={makeMapping({ status: "published", current_version_id: 3 })}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={onRevise}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={noop}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Revise mapping" }));
    await waitFor(() => expect(screen.getByText("↺ Revise")).toBeInTheDocument());
  });
});

describe("WorkspaceHeader focus mode (uiux bug report: canvas is too small)", () => {
  it("labels the toggle to enter focus mode and calls back on click", () => {
    const onToggleFocusMode = vi.fn();
    render(
      <WorkspaceHeader
        mapping={makeMapping()}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={noopAsync}
        validating={false}
        publishing={false}
        focusMode={false}
        onToggleFocusMode={onToggleFocusMode}
      />,
    );
    const button = screen.getByRole("button", { name: "Enter focus mode" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(button);
    expect(onToggleFocusMode).toHaveBeenCalledTimes(1);
  });

  it("labels the toggle to exit focus mode once active", () => {
    render(
      <WorkspaceHeader
        mapping={makeMapping()}
        role="admin"
        validation={null}
        onValidate={noop}
        onPublish={noop}
        onExport={noop}
        onRename={noopAsync}
        onReviewTransitioned={noop}
        onRevise={noopAsync}
        validating={false}
        publishing={false}
        focusMode
        onToggleFocusMode={noop}
      />,
    );
    const button = screen.getByRole("button", { name: "Exit focus mode" });
    expect(button).toHaveAttribute("aria-pressed", "true");
  });
});
