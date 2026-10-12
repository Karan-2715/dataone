import { describe, expect, it } from "vitest";
import {
  COMPARISON_OPERATORS,
  KIND_DESCRIPTIONS,
  TRANSFORMATION_KINDS,
  blankTransformation,
  validateTransformation,
} from "../transformations";

describe("case transformation kind (threshold conditional)", () => {
  it("is registered alongside the other 11 kinds", () => {
    expect(TRANSFORMATION_KINDS).toContain("case");
    expect(TRANSFORMATION_KINDS).toHaveLength(12);
  });

  it("exposes exactly 6 comparison operators", () => {
    expect(COMPARISON_OPERATORS.map((o) => o.value)).toEqual([
      ">", ">=", "<", "<=", "==", "!=",
    ]);
  });

  it("blankTransformation seeds a usable default that starts invalid until then/else are filled in", () => {
    const blank = blankTransformation("case");
    expect(blank).toEqual({ kind: "case", operator: ">", compare_value: 0, then_value: "", else_value: "" });
    const issues = validateTransformation(blank);
    expect(issues.map((i) => i.field).sort()).toEqual(["else_value", "then_value"]);
  });

  it("validates a fully-specified payload (annual_revenue > 500000000 -> Enterprise else SMB) as clean", () => {
    const issues = validateTransformation({
      kind: "case", operator: ">", compare_value: 500_000_000,
      then_value: "Enterprise", else_value: "SMB",
    });
    expect(issues).toEqual([]);
  });

  it("validates the numeric then/else example (employee_count > 0 -> 1 else 0) as clean", () => {
    // 0 is a legitimate value, not a missing field — must not be flagged.
    const issues = validateTransformation({
      kind: "case", operator: ">", compare_value: 0, then_value: 1, else_value: 0,
    });
    expect(issues).toEqual([]);
  });

  it("flags a missing operator/compare_value as required", () => {
    const issues = validateTransformation({
      kind: "case", operator: "" as never, compare_value: "" as never,
      then_value: "a", else_value: "b",
    });
    expect(issues.map((i) => i.field).sort()).toEqual(["compare_value", "operator"]);
  });

  it("has a description that does not collide with coalesce's (both were once labeled 'Conditional')", () => {
    expect(KIND_DESCRIPTIONS.case.label).not.toBe(KIND_DESCRIPTIONS.coalesce.label);
    expect(KIND_DESCRIPTIONS.coalesce.label).not.toMatch(/conditional/i);
  });
});
