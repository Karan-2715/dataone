import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Panel from "../Panel";

describe("Panel (Enterprise v2, E11-2)", () => {
  it("renders its content when in the normal state", () => {
    render(
      <Panel title="Properties" state="normal" onStateChange={() => {}} width={300} onWidthChange={() => {}}>
        <div>panel content</div>
      </Panel>,
    );
    expect(screen.getByText("panel content")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Properties panel" })).toBeInTheDocument();
  });

  it("collapses to a thin strip and hides content when minimized", () => {
    const { rerender } = render(
      <Panel title="Properties" state="normal" onStateChange={() => {}} width={300} onWidthChange={() => {}}>
        <div>panel content</div>
      </Panel>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Minimize Properties panel" }));
    // The click handler is asserted via onStateChange below; simulate the
    // parent re-rendering with the new collapsed state.
    rerender(
      <Panel title="Properties" state="collapsed" onStateChange={() => {}} width={300} onWidthChange={() => {}}>
        <div>panel content</div>
      </Panel>,
    );
    expect(screen.queryByText("panel content")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand Properties panel" })).toBeInTheDocument();
  });

  it("calls onStateChange(collapsed) when Minimize is clicked", () => {
    const onStateChange = vi.fn();
    render(
      <Panel title="Properties" state="normal" onStateChange={onStateChange} width={300} onWidthChange={() => {}}>
        <div>content</div>
      </Panel>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Minimize Properties panel" }));
    expect(onStateChange).toHaveBeenCalledWith("collapsed");
  });

  it("toggles maximized/restore on the Maximize button", () => {
    const onStateChange = vi.fn();
    const { rerender } = render(
      <Panel title="Properties" state="normal" onStateChange={onStateChange} width={300} onWidthChange={() => {}}>
        <div>content</div>
      </Panel>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Maximize Properties panel" }));
    expect(onStateChange).toHaveBeenCalledWith("maximized");

    rerender(
      <Panel title="Properties" state="maximized" onStateChange={onStateChange} width={300} onWidthChange={() => {}}>
        <div>content</div>
      </Panel>,
    );
    expect(screen.getByRole("button", { name: "Restore Properties panel" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore Properties panel" }));
    expect(onStateChange).toHaveBeenLastCalledWith("normal");
  });

  it("expands from collapsed back to normal", () => {
    const onStateChange = vi.fn();
    render(
      <Panel title="Properties" state="collapsed" onStateChange={onStateChange} width={300} onWidthChange={() => {}}>
        <div>content</div>
      </Panel>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Expand Properties panel" }));
    expect(onStateChange).toHaveBeenCalledWith("normal");
  });

  it("resizes with the keyboard on the splitter", () => {
    const onWidthChange = vi.fn();
    render(
      <Panel title="Properties" state="normal" onStateChange={() => {}} width={300} onWidthChange={onWidthChange}>
        <div>content</div>
      </Panel>,
    );
    const splitter = screen.getByRole("separator", { name: "Resize Properties panel" });
    fireEvent.keyDown(splitter, { key: "ArrowLeft" });
    expect(onWidthChange).toHaveBeenCalledWith(316);
    fireEvent.keyDown(splitter, { key: "ArrowRight" });
    expect(onWidthChange).toHaveBeenCalledWith(284);
  });

  it("clamps keyboard resize to the min/max bounds", () => {
    const onWidthChange = vi.fn();
    render(
      <Panel title="Properties" state="normal" onStateChange={() => {}} width={245} onWidthChange={onWidthChange}
        minWidth={240} maxWidth={640}>
        <div>content</div>
      </Panel>,
    );
    const splitter = screen.getByRole("separator", { name: "Resize Properties panel" });
    fireEvent.keyDown(splitter, { key: "ArrowRight" });
    expect(onWidthChange).toHaveBeenCalledWith(240);
  });

  it("does not render a splitter while maximized", () => {
    render(
      <Panel title="Properties" state="maximized" onStateChange={() => {}} width={300} onWidthChange={() => {}}>
        <div>content</div>
      </Panel>,
    );
    expect(screen.queryByRole("separator")).not.toBeInTheDocument();
  });
});
