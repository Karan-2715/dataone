import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KPITile, formatKPIValue } from "../components/KPITile";
import type { KPITileData } from "../types";

function tile(overrides: Partial<KPITileData> = {}): KPITileData {
  return {
    label: "Connected Sources",
    value: 5,
    subtitle: "3 database types",
    icon: "🔌",
    link_url: "/dashboard/connectors",
    module: "connectors",
    status: "loaded",
    ...overrides,
  };
}

describe("formatKPIValue", () => {
  it("shows small values with locale separators", () => {
    expect(formatKPIValue(0)).toBe("0");
    expect(formatKPIValue(1234)).toBe("1,234");
    expect(formatKPIValue(9999)).toBe("9,999");
  });

  it("abbreviates values >= 10k with a k suffix", () => {
    expect(formatKPIValue(10_000)).toBe("10.0k");
    expect(formatKPIValue(12_345)).toBe("12.3k");
  });

  it("abbreviates values >= 1M with an M suffix", () => {
    expect(formatKPIValue(1_234_567)).toBe("1.2M");
  });
});

describe("KPITile", () => {
  it("renders value, label, and subtitle", () => {
    render(<KPITile tile={tile()} />);
    expect(screen.getByText("Connected Sources")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("3 database types")).toBeInTheDocument();
  });

  it("shows zero as 0, not a placeholder dash", () => {
    render(<KPITile tile={tile({ value: 0 })} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("is a drill-through link when loaded", () => {
    render(<KPITile tile={tile()} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/dashboard/connectors");
  });

  it("is not a link in error state and shows the error message", () => {
    render(
      <KPITile
        tile={tile({ status: "error", error_message: "audit table unreachable" })}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("audit table unreachable")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("is not a link when unavailable (restricted module)", () => {
    render(
      <KPITile
        tile={tile({
          status: "unavailable",
          error_message: "You do not have permission to view this data.",
        })}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(
      screen.getByText("You do not have permission to view this data."),
    ).toBeInTheDocument();
  });

  it("shows an up-trend indicator when trend=up and value > 0", () => {
    render(<KPITile tile={tile({ label: "Pipelines Failed", value: 3, trend: "up" })} />);
    expect(screen.getByText("↑")).toBeInTheDocument();
  });

  it("hides the trend indicator when the value is zero", () => {
    render(<KPITile tile={tile({ value: 0, trend: "up" })} />);
    expect(screen.queryByText("↑")).not.toBeInTheDocument();
  });

  it("hides the trend indicator when trend is neutral", () => {
    render(<KPITile tile={tile({ value: 3, trend: "neutral" })} />);
    expect(screen.queryByText("↑")).not.toBeInTheDocument();
    expect(screen.queryByText("↓")).not.toBeInTheDocument();
  });

  it("renders a skeleton (no value, no link) while loading", () => {
    const { container } = render(<KPITile isLoading tile={tile()} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText("Connected Sources")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
  });
});
