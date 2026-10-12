import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SegmentedControl, WorkspaceHeader } from "../WorkspaceHeader";

describe("WorkspaceHeader", () => {
  it("renders context, description, and actions", () => {
    render(
      <WorkspaceHeader
        eyebrow="Operations"
        title="Connectors"
        description="Manage database connections."
        actions={<button type="button">New connector</button>}
      />,
    );

    expect(screen.getByRole("heading", { name: "Connectors" })).toBeInTheDocument();
    expect(screen.getByText("Operations")).toBeInTheDocument();
    expect(screen.getByText("Manage database connections.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New connector" })).toBeInTheDocument();
  });
});

describe("SegmentedControl", () => {
  it("reports selection and changes mode", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Workspace mode"
        value="ask"
        onChange={onChange}
        options={[
          { value: "ask", label: "Ask" },
          { value: "sql", label: "SQL", indicator: true },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Ask" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Completed in background")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /SQL/ }));
    expect(onChange).toHaveBeenCalledWith("sql");
  });
});
