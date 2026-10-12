import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimeRangeFilter } from "../components/TimeRangeFilter";
import type { TimeRange } from "../types";

describe("TimeRangeFilter", () => {
  it("renders the three range options", () => {
    render(<TimeRangeFilter value="7d" onChange={() => {}} />);
    expect(screen.getByText("24h")).toBeInTheDocument();
    expect(screen.getByText("7 days")).toBeInTheDocument();
    expect(screen.getByText("30 days")).toBeInTheDocument();
  });

  it("marks the selected option as checked", () => {
    render(<TimeRangeFilter value="7d" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "7 days" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "24h" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("calls onChange with the clicked range", () => {
    const onChange = vi.fn();
    render(<TimeRangeFilter value="7d" onChange={onChange} />);
    fireEvent.click(screen.getByText("24h"));
    expect(onChange).toHaveBeenCalledWith("24h");
  });

  it("disables all buttons when disabled", () => {
    render(<TimeRangeFilter value="7d" onChange={() => {}} disabled />);
    screen.getAllByRole("radio").forEach((btn) => expect(btn).toBeDisabled());
  });

  it("falls back to 7d for an unknown value", () => {
    render(<TimeRangeFilter value={"90d" as TimeRange} onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "7 days" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
