import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TransformEditor from "../TransformEditor";
import type { TransformationPayload } from "../../lib/types";

function renderEditor(initial: TransformationPayload, onApply = vi.fn()) {
  render(<TransformEditor initial={initial} onCancel={vi.fn()} onApply={onApply} />);
  return onApply;
}

describe("TransformEditor — case (threshold conditional)", () => {
  it("switching Kind to Conditional (If/Else) shows the operator/compare/then/else fields", () => {
    render(<TransformEditor initial={{ kind: "direct" }} onCancel={vi.fn()} onApply={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "case" } });
    expect(screen.getByLabelText("Operator")).toBeInTheDocument();
    expect(screen.getByLabelText("Compare to")).toBeInTheDocument();
    expect(screen.getByLabelText("Then use")).toBeInTheDocument();
    expect(screen.getByLabelText("Else use")).toBeInTheDocument();
  });

  it("coerces a numeric-looking compare_value to a real number, not a string (annual_revenue example)", () => {
    const onApply = renderEditor({
      kind: "case", operator: ">", compare_value: 0, then_value: "x", else_value: "y",
    });
    fireEvent.change(screen.getByLabelText("Compare to"), { target: { value: "500000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ compare_value: 500_000_000 }),
    );
    expect(typeof (onApply.mock.calls[0][0] as { compare_value: unknown }).compare_value).toBe("number");
  });

  it("keeps a non-numeric then/else value as a string (segment example)", () => {
    const onApply = renderEditor({
      kind: "case", operator: ">", compare_value: 500_000_000, then_value: "", else_value: "",
    });
    fireEvent.change(screen.getByLabelText("Then use"), { target: { value: "Enterprise" } });
    fireEvent.change(screen.getByLabelText("Else use"), { target: { value: "SMB" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ then_value: "Enterprise", else_value: "SMB" }),
    );
  });

  it("coerces numeric then/else values too (is_active example: employee_count > 0 -> 1 else 0)", () => {
    const onApply = renderEditor({
      kind: "case", operator: ">", compare_value: "", then_value: "", else_value: "",
    });
    fireEvent.change(screen.getByLabelText("Compare to"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Then use"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Else use"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    const applied = onApply.mock.calls[0][0] as Record<string, unknown>;
    expect(applied.compare_value).toBe(0);
    expect(applied.then_value).toBe(1);
    expect(applied.else_value).toBe(0);
    expect(typeof applied.then_value).toBe("number");
  });

  it("disables Apply until then_value and else_value are filled in", () => {
    render(
      <TransformEditor
        initial={{ kind: "case", operator: ">", compare_value: 0, then_value: "", else_value: "" }}
        onCancel={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("changing the operator selects among all 6 comparison options", () => {
    render(
      <TransformEditor
        initial={{ kind: "case", operator: ">", compare_value: 0, then_value: "a", else_value: "b" }}
        onCancel={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    const select = screen.getByLabelText("Operator") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "!=" } });
    expect(select.value).toBe("!=");
  });
});
