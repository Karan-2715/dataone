import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardWidget } from "../components/DashboardWidget";

const base = {
  title: "Recent Activity",
  isLoading: false,
  isEmpty: false,
  isError: false,
};

describe("DashboardWidget", () => {
  it("renders children in the happy path", () => {
    render(
      <DashboardWidget {...base}>
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.getByText("widget content")).toBeInTheDocument();
  });

  it("shows a skeleton while loading, hiding content", () => {
    const { container } = render(
      <DashboardWidget {...base} isLoading>
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.queryByText("widget content")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
  });

  it("shows the error message and a retry button that calls onRetry", () => {
    const onRetry = vi.fn();
    render(
      <DashboardWidget {...base} isError errorMessage="boom" onRetry={onRetry}>
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByText("widget content")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("omits the retry button when onRetry is not provided", () => {
    render(
      <DashboardWidget {...base} isError errorMessage="boom">
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the empty state with an action link", () => {
    render(
      <DashboardWidget
        {...base}
        isEmpty
        emptyMessage="No connections yet."
        emptyAction={{ label: "Add one", href: "/dashboard/connectors" }}
      >
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.getByText("No connections yet.")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/dashboard/connectors");
    expect(screen.queryByText("widget content")).not.toBeInTheDocument();
  });

  it("applies state precedence loading > error > empty", () => {
    const { container } = render(
      <DashboardWidget {...base} isLoading isError isEmpty errorMessage="boom">
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
  });

  it("links the header for drill-through when loaded", () => {
    render(
      <DashboardWidget {...base} linkUrl="/dashboard/audit">
        <p>widget content</p>
      </DashboardWidget>,
    );
    expect(screen.getByRole("link", { name: /Recent Activity/ })).toHaveAttribute(
      "href",
      "/dashboard/audit",
    );
  });
});
