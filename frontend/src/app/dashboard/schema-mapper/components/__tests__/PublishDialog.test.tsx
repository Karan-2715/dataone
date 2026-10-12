import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PublishDialog from "../PublishDialog";

describe("PublishDialog (uiux bug report: publish popup looks stuck)", () => {
  it("shows a persistent inline error banner after a failed publish attempt", () => {
    render(
      <PublishDialog
        open
        blockingCount={0}
        warningCount={0}
        currentVersionId={1}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        publishing={false}
        error="target is NOT NULL but source is nullable and no null-handling transform provided"
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Publish failed:");
    expect(alert).toHaveTextContent(
      "target is NOT NULL but source is nullable and no null-handling transform provided",
    );
  });

  it("shows no error banner when there is nothing to report", () => {
    render(
      <PublishDialog
        open
        blockingCount={0}
        warningCount={0}
        currentVersionId={1}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        publishing={false}
        error={null}
      />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still allows retrying (Publish stays enabled) after a failed attempt with 0 blocking issues", () => {
    const onConfirm = vi.fn();
    render(
      <PublishDialog
        open
        blockingCount={0}
        warningCount={0}
        currentVersionId={1}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        publishing={false}
        error="Publish failed. Please try again."
      />,
    );
    const publishButton = screen.getByRole("button", { name: /Publish v2/ });
    expect(publishButton).not.toBeDisabled();
    fireEvent.click(publishButton);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables Publish and shows the blocking-issue banner when blockingCount > 0, independent of error", () => {
    render(
      <PublishDialog
        open
        blockingCount={3}
        warningCount={0}
        currentVersionId={1}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        publishing={false}
        error={null}
      />,
    );
    expect(screen.getByText(/Resolve 3 blocking issues before publishing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Publish v2/ })).toBeDisabled();
  });

  it("does not close via backdrop click while publishing is in progress", () => {
    const onCancel = vi.fn();
    render(
      <PublishDialog
        open
        blockingCount={0}
        warningCount={0}
        currentVersionId={1}
        onCancel={onCancel}
        onConfirm={vi.fn()}
        publishing
        error={null}
      />,
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
