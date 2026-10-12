import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityFeed } from "../components/ActivityFeed";
import type { FeedItemData } from "../types";

function item(overrides: Partial<FeedItemData> = {}): FeedItemData {
  return {
    id: 1,
    event_type: "connector_created",
    actor: "admin@test.local",
    module: "connectors",
    summary: "Connector created — Src",
    status: "success",
    created_at: new Date().toISOString(),
    link_url: "/dashboard/connectors",
    ...overrides,
  };
}

describe("ActivityFeed", () => {
  it("renders item summary, actor, and drill-through link", () => {
    render(<ActivityFeed items={[item()]} isLoading={false} isError={false} />);
    expect(screen.getByText("Connector created — Src")).toBeInTheDocument();
    expect(screen.getByText(/admin@test.local/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/dashboard/connectors");
  });

  it("renders items without link_url as non-links", () => {
    render(
      <ActivityFeed
        items={[item({ link_url: null })]}
        isLoading={false}
        isError={false}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Connector created — Src")).toBeInTheDocument();
  });

  it("tints failures and shows a Failed badge", () => {
    render(
      <ActivityFeed
        items={[item({ status: "failure" })]}
        isLoading={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("shows the empty state when there are no items", () => {
    render(<ActivityFeed items={[]} isLoading={false} isError={false} />);
    expect(screen.getByText(/No activity yet/)).toBeInTheDocument();
  });

  it("truncates to 8 items and links to the full audit trail", () => {
    const items = Array.from({ length: 10 }, (_, i) =>
      item({ id: i + 1, summary: `Event ${i + 1}` }),
    );
    render(<ActivityFeed items={items} isLoading={false} isError={false} />);
    expect(screen.getByText("Event 8")).toBeInTheDocument();
    expect(screen.queryByText("Event 9")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View all activity/ })).toHaveAttribute(
      "href",
      "/dashboard/audit",
    );
  });

  it("falls back to the raw event_type label for unknown events", () => {
    render(
      <ActivityFeed
        items={[item({ event_type: "totally_new_event", summary: "" })]}
        isLoading={false}
        isError={false}
      />,
    );
    expect(screen.getByText("totally_new_event")).toBeInTheDocument();
  });
});
