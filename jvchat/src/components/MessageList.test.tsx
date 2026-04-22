import { render, screen } from "@testing-library/react";
import { MessageList } from "./MessageList";
import type { Message } from "../types/message";

function message(overrides: Partial<Message>): Message {
  return {
    id: overrides.id || "m-1",
    role: overrides.role || "assistant",
    content: overrides.content || "",
    timestamp: overrides.timestamp || "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("MessageList thought rendering", () => {
  it("renders grouped thinking panel before assistant response", () => {
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

    render(<MessageList messages={messages} />);

    expect(screen.getByText("User asks a question")).toBeInTheDocument();
    expect(screen.getByText("Thoughts · 1 · click to expand")).toBeInTheDocument();
    expect(
      screen.getByText(/\{"tool":"search","query":"weather"\}/),
    ).toBeInTheDocument();
    expect(screen.getByText("Found 3 matching records.")).toBeInTheDocument();
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

    const { container } = render(<MessageList messages={messages} />);

    const thoughtBubble = container.querySelector(
      '[data-message-category="thought"]',
    );
    const assistantBubble = container.querySelector('[data-message-role="assistant"]');

    expect(thoughtBubble).toBeNull();
    expect(assistantBubble).toBeTruthy();
    expect(screen.getByText("Thoughts · 1 · click to expand")).toBeInTheDocument();
  });

  it("renders an orphan Thinking panel before any assistant anchor exists", () => {
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

    const { container } = render(<MessageList messages={messages} />);

    const orphanPanel = container.querySelector(
      '[data-thinking-panel="orphan"][data-interaction-id="int-3"]',
    );
    expect(orphanPanel).toBeTruthy();
    expect(orphanPanel?.querySelector("details")?.open).toBe(true);
    expect(screen.getByText("Planning to call web search")).toBeInTheDocument();
    expect(screen.getByText(/I'll search with web_search\./)).toBeInTheDocument();
  });

  it("auto-opens the anchored Thinking panel while thoughts are streaming", () => {
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

    const { container } = render(<MessageList messages={messages} />);

    const anchoredPanel = container.querySelector(
      '[data-thinking-panel="anchored"][data-interaction-id="int-4"]',
    );
    expect(anchoredPanel).toBeTruthy();
    expect(anchoredPanel?.querySelector("details")?.open).toBe(true);
  });

  it("scopes thoughts to the turn they arrived in (no bleed into prior panels)", () => {
    // Turn 1: user -> thought -> assistant
    // Turn 2: user -> thought -> assistant (different interactionId)
    // Even if turn 2's thoughts re-use tags from turn 1 metadata, each panel
    // should only show its own turn's thoughts.
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

    const { container } = render(<MessageList messages={messages} />);

    const panels = container.querySelectorAll('[data-thinking-panel="anchored"]');
    expect(panels.length).toBe(2);

    // Each panel should contain exactly one thought - its own.
    const firstPanelText = panels[0].textContent || "";
    const secondPanelText = panels[1].textContent || "";
    expect(firstPanelText).toContain("First turn reasoning");
    expect(firstPanelText).not.toContain("Second turn reasoning");
    expect(secondPanelText).toContain("Second turn reasoning");
    expect(secondPanelText).not.toContain("First turn reasoning");
  });

  it("renders multi-paragraph thoughts as separate blocks", () => {
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

    render(<MessageList messages={messages} />);

    expect(screen.getByText(/First block\./)).toBeInTheDocument();
    expect(screen.getByText("Second block here.")).toBeInTheDocument();
  });
});
