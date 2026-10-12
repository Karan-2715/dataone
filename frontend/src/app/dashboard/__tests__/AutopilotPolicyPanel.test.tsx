import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PolicyPanel from "../autopilot/components/PolicyPanel";
import type { AutopilotPolicyEntry } from "../autopilot/lib/types";

function policy(
  overrides: Partial<AutopilotPolicyEntry> = {},
): AutopilotPolicyEntry {
  return {
    action_type: "connector_health_check",
    autonomy: "suggest",
    max_auto_per_hour: 10,
    description: "Re-test a degraded connection",
    risk: "low",
    reversible: true,
    reversibility_note: "Read-only probe",
    auto_capable: true,
    updated_by: null,
    ...overrides,
  };
}

describe("PolicyPanel", () => {
  it("renders one row per action type with risk and reversibility badges", () => {
    render(
      <PolicyPanel
        policies={[
          policy(),
          policy({
            action_type: "migration_execute",
            risk: "high",
            reversible: false,
            auto_capable: false,
          }),
        ]}
        role="admin"
        savingType={null}
        onSave={() => {}}
      />,
    );
    expect(screen.getByText("connector_health_check")).toBeInTheDocument();
    expect(screen.getByText("migration_execute")).toBeInTheDocument();
    expect(screen.getByText("high risk")).toBeInTheDocument();
    expect(screen.getByText("irreversible")).toBeInTheDocument();
  });

  it("disables the auto option for non-auto-capable actions", () => {
    render(
      <PolicyPanel
        policies={[
          policy({
            action_type: "migration_execute",
            risk: "high",
            reversible: false,
            auto_capable: false,
          }),
        ]}
        role="admin"
        savingType={null}
        onSave={() => {}}
      />,
    );
    const select = screen.getByLabelText("Autonomy for migration_execute");
    const auto = Array.from(select.querySelectorAll("option")).find((o) =>
      o.value === "auto",
    );
    expect(auto).toBeDefined();
    expect(auto!.disabled).toBe(true);
  });

  it("keeps the auto option enabled for auto-capable actions", () => {
    render(
      <PolicyPanel
        policies={[policy()]}
        role="admin"
        savingType={null}
        onSave={() => {}}
      />,
    );
    const select = screen.getByLabelText("Autonomy for connector_health_check");
    const auto = Array.from(select.querySelectorAll("option")).find((o) =>
      o.value === "auto",
    );
    expect(auto!.disabled).toBe(false);
  });

  it("is read-only for non-admin roles", () => {
    render(
      <PolicyPanel
        policies={[policy()]}
        role="analyst"
        savingType={null}
        onSave={() => {}}
      />,
    );
    expect(
      screen.getByLabelText("Autonomy for connector_health_check"),
    ).toBeDisabled();
    expect(screen.getByText(/Read-only/)).toBeInTheDocument();
    expect(screen.queryByText("Save")).not.toBeInTheDocument();
  });

  it("shows Save only after an edit and calls onSave with the new values", () => {
    const onSave = vi.fn();
    render(
      <PolicyPanel
        policies={[policy()]}
        role="admin"
        savingType={null}
        onSave={onSave}
      />,
    );
    expect(screen.queryByText("Save")).not.toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("Autonomy for connector_health_check"),
      { target: { value: "auto" } },
    );
    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalledWith("connector_health_check", "auto", 10);
  });
});
