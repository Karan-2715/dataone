import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DialogSurface } from "../DialogSurface";

describe("DialogSurface", () => {
  it("provides dialog semantics and closes from its button", () => {
    const onClose = vi.fn();
    render(<DialogSurface title="Edit connector" description="Update settings" onClose={onClose}>Form</DialogSurface>);

    expect(screen.getByRole("dialog", { name: "Edit connector" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Update settings")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close Edit connector" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on Escape and backdrop click", () => {
    const onClose = vi.fn();
    render(<DialogSurface title="Delete connector?" onClose={onClose}>Warning</DialogSurface>);

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(screen.getByRole("presentation"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
