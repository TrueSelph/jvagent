import type { ReactElement } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider } from "../context/ThemeContext";
import { Thread } from "./Thread";
import type { Message } from "../types/message";

function noopSend() {}

function renderThread(ui: ReactElement) {
  return render(<ThemeProvider>{ui}</ThemeProvider>);
}

function message(overrides: Partial<Message>): Message {
  return {
    id: overrides.id || "m-1",
    role: overrides.role || "assistant",
    content: overrides.content || "",
    timestamp: overrides.timestamp || "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("Thread thought rendering", () => {
  it("renders grouped reasoning panel before assistant response", async () => {
    const messages: Message[] = [
      message({
        id: "u-1",
        role: "user",
        content: "User asks a question",
        timestamp: "2026-01-01T00:00:00.000Z",
      }),
      message({
        id: "t-1",
        role: "assistant",
        category: "thought",
        thoughtType: "tool_call",
        content: '{"tool":"search","query":"weather"}',
        interactionId: "int-1",
        timestamp: "2026-01-01T00:00:01.000Z",
      }),
      message({
        id: "a-1",
        role: "assistant",
        content: "Found 3 matching records.",
        interactionId: "int-1",
        timestamp: "2026-01-01T00:00:02.000Z",
      }),
    ];

    renderThread(<Thread messages={messages} onSend={noopSend} />);

    expect(screen.getByText("User asks a question")).toBeInTheDocument();
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
    expect(screen.getByText("Found 3 matching records.")).toBeInTheDocument();

    // Click to open the reasoning panel
    screen.getByText("Reasoning").click();

    await waitFor(() => {
      expect(
        screen.getByText(/\{"tool":"search","query":"weather"\}/),
      ).toBeInTheDocument();
    });
  });

  it("does not render standalone thought bubbles", () => {
    const messages: Message[] = [
      message({
        id: "t-2",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "Reasoning step",
        interactionId: "int-2",
      }),
      message({
        id: "a-2",
        role: "assistant",
        content: "Answer bubble",
        interactionId: "int-2",
      }),
    ];

    const { container } = renderThread(
      <Thread messages={messages} onSend={noopSend} />,
    );

    const thoughtBubble = container.querySelector(
      '[data-message-category="thought"]',
    );
    const assistantBubble = container.querySelector(
      '[data-message-role="assistant"]',
    );

    expect(thoughtBubble).toBeNull();
    expect(assistantBubble).toBeTruthy();
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
  });

  it("renders an orphan Reasoning panel before any assistant anchor exists", () => {
    const messages: Message[] = [
      message({
        id: "u-3",
        role: "user",
        content: "Search the web for Eldon Marks",
        timestamp: "2026-01-01T00:00:00.000Z",
      }),
      message({
        id: "t-3a",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "Planning to call web search",
        interactionId: "int-3",
        timestamp: "2026-01-01T00:00:00.500Z",
        streaming: true,
      }),
      message({
        id: "t-3b",
        role: "assistant",
        category: "thought",
        thoughtType: "tool_call",
        content: "I'll search with web_search.",
        interactionId: "int-3",
        timestamp: "2026-01-01T00:00:00.800Z",
        streaming: true,
      }),
    ];

    const { container } = renderThread(
      <Thread messages={messages} onSend={noopSend} />,
    );

    // The orphan reasoning panel should exist with the right data attrs
    const orphanPanel = container.querySelector(
      '[data-reasoning-panel="orphan"][data-interaction-id="int-3"]',
    );
    expect(orphanPanel).toBeTruthy();
    // Streaming thoughts auto-open, so content should be visible
    expect(screen.getByText("Planning to call web search")).toBeInTheDocument();
    expect(screen.getByText(/I'll search with web_search\./)).toBeInTheDocument();
  });

  it("auto-opens the anchored Reasoning panel while thoughts are streaming", () => {
    const messages: Message[] = [
      message({
        id: "u-4",
        role: "user",
        content: "Do the thing",
        timestamp: "2026-01-01T00:00:00.000Z",
      }),
      message({
        id: "t-4",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "Reasoning in progress",
        interactionId: "int-4",
        timestamp: "2026-01-01T00:00:00.500Z",
        streaming: true,
      }),
      message({
        id: "a-4",
        role: "assistant",
        content: "Partial answer streaming",
        interactionId: "int-4",
        timestamp: "2026-01-01T00:00:00.800Z",
        streaming: true,
      }),
    ];

    const { container } = renderThread(
      <Thread messages={messages} onSend={noopSend} />,
    );

    const anchoredPanel = container.querySelector(
      '[data-reasoning-panel="anchored"][data-interaction-id="int-4"]',
    );
    expect(anchoredPanel).toBeTruthy();
    // Streaming panel should show the content
    expect(screen.getByText("Reasoning in progress")).toBeInTheDocument();
  });

  it("scopes thoughts to the turn they arrived in (no bleed into prior panels)", async () => {
    const messages: Message[] = [
      message({
        id: "u-t1",
        role: "user",
        content: "Question one",
        timestamp: "2026-01-01T00:00:00.000Z",
      }),
      message({
        id: "th-t1",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "First turn reasoning",
        interactionId: "int-turn-1",
        timestamp: "2026-01-01T00:00:01.000Z",
      }),
      message({
        id: "a-t1",
        role: "assistant",
        content: "Answer one",
        interactionId: "int-turn-1",
        timestamp: "2026-01-01T00:00:02.000Z",
      }),
      message({
        id: "u-t2",
        role: "user",
        content: "Question two",
        timestamp: "2026-01-01T00:00:03.000Z",
      }),
      message({
        id: "th-t2",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "Second turn reasoning",
        interactionId: "int-turn-2",
        timestamp: "2026-01-01T00:00:04.000Z",
      }),
      message({
        id: "a-t2",
        role: "assistant",
        content: "Answer two",
        interactionId: "int-turn-2",
        timestamp: "2026-01-01T00:00:05.000Z",
      }),
    ];

    const { container } = renderThread(
      <Thread messages={messages} onSend={noopSend} />,
    );

    const panels = container.querySelectorAll(
      '[data-reasoning-panel="anchored"]',
    );
    expect(panels.length).toBe(2);

    // Click each to open
    const reasoningButtons = screen.getAllByText("Reasoning");
    reasoningButtons.forEach((btn) => btn.click());

    // Each panel should contain its own turn's thought
    await waitFor(() => {
      const firstPanelText = panels[0].textContent || "";
      expect(firstPanelText).toContain("First turn reasoning");
    });
    const firstPanelText = panels[0].textContent || "";
    const secondPanelText = panels[1].textContent || "";
    expect(firstPanelText).not.toContain("Second turn reasoning");
    expect(secondPanelText).toContain("Second turn reasoning");
    expect(secondPanelText).not.toContain("First turn reasoning");
  });

  it("renders multi-paragraph thoughts as separate blocks", async () => {
    const messages: Message[] = [
      message({
        id: "t-mp",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "First block.\n\nSecond block here.",
        interactionId: "int-mp",
        timestamp: "2026-01-01T00:00:01.000Z",
      }),
      message({
        id: "a-mp",
        role: "assistant",
        content: "Answer",
        interactionId: "int-mp",
        timestamp: "2026-01-01T00:00:02.000Z",
      }),
    ];

    renderThread(<Thread messages={messages} onSend={noopSend} />);

    // Click to open the reasoning panel
    screen.getByText("Reasoning").click();

    await waitFor(() => {
      expect(screen.getByText(/First block\./)).toBeInTheDocument();
    });
    expect(screen.getByText("Second block here.")).toBeInTheDocument();
  });
});
