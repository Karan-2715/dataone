import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GlobalSearchPalette from "../GlobalSearchPalette";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock } }));

describe("GlobalSearchPalette", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({
      query: "sales", total: 1, results: [{
        kind: "column", connection_id: 7, connection_name: "Warehouse",
        table_id: 12, table_name: "orders", column_id: 44,
        column_name: "sales_total", data_type: "DECIMAL",
      }],
    });
  });

  it("opens with Ctrl-K, debounces search, and links to the selected catalog result", async () => {
    render(<GlobalSearchPalette />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Global catalog search" })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Search query" }), {
      target: { value: "sales" },
    });
    expect(getMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("sales_total")).toBeInTheDocument());

    expect(getMock).toHaveBeenCalledWith(
      "/api/v1/search?q=sales&page_size=20",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByRole("link", { name: /sales_total/ })).toHaveAttribute(
      "href",
      "/dashboard/schema?connection_id=7&q=sales_total",
    );
  });

  it("requires two characters and closes on Escape", () => {
    render(<GlobalSearchPalette />);
    fireEvent.click(screen.getByRole("button", { name: "Search connections, tables, and columns" }));
    expect(screen.getByText("Enter at least 2 characters.")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("exposes expanded state on the trigger and restores focus to it on close (E01-11)", () => {
    render(<GlobalSearchPalette />);
    const trigger = screen.getByRole("button", { name: "Search connections, tables, and columns" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog")).toHaveAttribute("id", "global-search-dialog");
    expect(trigger).toHaveAttribute("aria-controls", "global-search-dialog");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("traps Tab focus within the dialog (E01-11)", () => {
    render(<GlobalSearchPalette />);
    fireEvent.click(screen.getByRole("button", { name: "Search connections, tables, and columns" }));

    const dialog = screen.getByRole("dialog");
    const input = screen.getByRole("textbox", { name: "Search query" });
    const closeButton = screen.getByRole("button", { name: "Close search" });

    // Shift+Tab from the first focusable wraps to the last.
    input.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(closeButton).toHaveFocus();

    // Tab from the last focusable wraps back to the first.
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(input).toHaveFocus();
  });
});
