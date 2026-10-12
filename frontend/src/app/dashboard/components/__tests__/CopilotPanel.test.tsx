import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CopilotPanel from "../CopilotPanel";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock, post: postMock } }));

describe("CopilotPanel", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockResolvedValue([{ id: 1, name: "Prod DB" }, { id: 2, name: "Staging DB" }]);
  });

  it("opens on click and loads connections lazily", async () => {
    render(<CopilotPanel />);
    expect(getMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    expect(screen.getByRole("dialog", { name: "AI Copilot" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "Prod DB" })).toBeInTheDocument();
  });

  it("asks a question and renders the grounded answer with its intent label", async () => {
    postMock.mockResolvedValue({
      session_id: "s1", summary: "3 open risk finding(s) — 1 critical, 2 high.", error: null,
      intent: "platform_insight", sql: null, rows: [], row_count: 0, platform_insight: { risk: { total: 3 } },
    });
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "what are the critical risks?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText(/3 open risk finding/)).toBeInTheDocument();
    expect(screen.getByText("Platform Insight")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/api/v1/askdata/ask", {
      connection_id: 1, question: "what are the critical risks?", session_id: undefined,
    });
  });

  it("reuses the session_id returned by the backend on the next turn", async () => {
    postMock.mockResolvedValueOnce({
      session_id: "s1", summary: "First answer", error: null, intent: "read_query",
      sql: null, rows: [], row_count: 0, platform_insight: null,
    });
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("First answer");

    postMock.mockResolvedValueOnce({
      session_id: "s1", summary: "Second answer", error: null, intent: "read_query",
      sql: null, rows: [], row_count: 0, platform_insight: null,
    });
    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "second" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("Second answer");

    expect(postMock).toHaveBeenLastCalledWith("/api/v1/askdata/ask", {
      connection_id: 1, question: "second", session_id: "s1",
    });
  });

  it("starts a new conversation when the connection changes", async () => {
    postMock.mockResolvedValue({
      session_id: "s1", summary: "First answer", error: null, intent: "read_query",
      sql: null, rows: [], row_count: 0, platform_insight: null,
    });
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    const select = await screen.findByRole("combobox", { name: "Copilot connection context" });
    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("First answer");

    fireEvent.change(select, { target: { value: "2" } });
    expect(screen.queryByText("First answer")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "new context" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(postMock).toHaveBeenLastCalledWith("/api/v1/askdata/ask", {
      connection_id: 2, question: "new context", session_id: undefined,
    }));
  });

  it("hides the New-conversation button until there is a conversation to clear (B-E09-12)", async () => {
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Start new conversation" })).not.toBeInTheDocument();
  });

  it("clears turns and the session on New conversation, without closing the panel (B-E09-12)", async () => {
    postMock.mockResolvedValueOnce({
      session_id: "s1", summary: "First answer", error: null, intent: "read_query",
      sql: null, rows: [], row_count: 0, platform_insight: null,
    });
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("First answer");

    fireEvent.click(screen.getByRole("button", { name: "Start new conversation" }));
    expect(screen.queryByText("First answer")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "AI Copilot" })).toBeInTheDocument();

    postMock.mockResolvedValueOnce({
      session_id: "s2", summary: "Second answer", error: null, intent: "read_query",
      sql: null, rows: [], row_count: 0, platform_insight: null,
    });
    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "second" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(postMock).toHaveBeenLastCalledWith("/api/v1/askdata/ask", {
      connection_id: 1, question: "second", session_id: undefined,
    }));
  });

  it("traps focus and restores it to the trigger on close", async () => {
    render(<CopilotPanel />);
    const trigger = screen.getByRole("button", { name: "Open AI Copilot" });
    fireEvent.click(trigger);
    const select = await screen.findByRole("combobox", { name: "Copilot connection context" });
    await waitFor(() => expect(select).toHaveFocus());
    fireEvent.keyDown(select, { key: "Tab", shiftKey: true });
    expect(screen.getByPlaceholderText("Ask the copilot…")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("shows a graceful message when the backend call fails, never a crash", async () => {
    postMock.mockRejectedValue(new Error("copilot backend unreachable"));
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask the copilot…"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("copilot backend unreachable")).toBeInTheDocument();
  });

  it("closes on Escape and on outside click", async () => {
    render(
      <div>
        <CopilotPanel />
        <button type="button">Outside</button>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    fireEvent.mouseDown(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables Ask until a question is typed and a connection is loaded", async () => {
    render(<CopilotPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open AI Copilot" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Copilot connection context" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });
});
