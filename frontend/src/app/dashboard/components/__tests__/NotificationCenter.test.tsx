import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NotificationCenter from "../NotificationCenter";

const { getMock, patchMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock, patch: patchMock } }));

describe("NotificationCenter", () => {
  beforeEach(() => {
    getMock.mockReset();
    patchMock.mockReset();
    patchMock.mockResolvedValue({});
    getMock.mockImplementation((path: string) => {
      if (path.includes("unread-count")) return Promise.resolve({ unread: 2 });
      return Promise.resolve({
        notifications: [
          { id: 1, event_key: "drift", title: "Drift detected", body: "orders table", link: "/dashboard/schema", created_at: new Date().toISOString(), read: false },
        ],
      });
    });
  });

  it("exposes expanded/haspopup state on the bell and opens the panel (E01-11)", async () => {
    render(<NotificationCenter />);
    const bell = screen.getByRole("button", { name: /Notifications/ });
    expect(bell).toHaveAttribute("aria-expanded", "false");
    expect(bell).toHaveAttribute("aria-haspopup", "dialog");
    expect(bell).toHaveAttribute("aria-controls", "notification-center-panel");

    fireEvent.click(bell);
    expect(bell).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(screen.getByText("Drift detected")).toBeInTheDocument());
    expect(screen.getByRole("region", { name: "Notification center" })).toBeInTheDocument();
  });

  it("closes on Escape (E01-11)", async () => {
    render(<NotificationCenter />);
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    await waitFor(() => expect(screen.getByText("Drift detected")).toBeInTheDocument());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("Drift detected")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Notifications/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on outside click (E01-11)", async () => {
    render(
      <div>
        <NotificationCenter />
        <button type="button">Outside</button>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    await waitFor(() => expect(screen.getByText("Drift detected")).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByText("Drift detected")).not.toBeInTheDocument();
  });
});
