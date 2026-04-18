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
  it("renders thought messages inline with thought-specific formatting", () => {
    const messages: Message[] = [
      message({
        id: "u-1",
        role: "user",
        content: "User asks a question",
        timestamp: "2026-01-01T00:00:00.000Z",
      }),
    ];

    const thoughtMessages: Message[] = [
      message({
        id: "t-1",
        role: "assistant",
        category: "thought",
        thoughtType: "tool_call",
        content: '{"tool":"search","query":"weather"}',
        timestamp: "2026-01-01T00:00:01.000Z",
      }),
    ];

    render(<MessageList messages={messages} thoughtMessages={thoughtMessages} />);

    expect(screen.getByText("User asks a question")).toBeInTheDocument();
    expect(screen.getByText("Tool Call")).toBeInTheDocument();
    expect(screen.getByText("internal stream")).toBeInTheDocument();
    expect(
      screen.getByText('{"tool":"search","query":"weather"}'),
    ).toBeInTheDocument();
    expect(screen.queryByText("Thinking & Tooling Stream")).not.toBeInTheDocument();
  });

  it("keeps user and thought bubbles visually distinct", () => {
    const messages: Message[] = [
      message({
        id: "u-2",
        role: "user",
        content: "User bubble",
      }),
    ];
    const thoughtMessages: Message[] = [
      message({
        id: "t-2",
        role: "assistant",
        category: "thought",
        thoughtType: "reasoning",
        content: "Reasoning step",
      }),
    ];

    const { container } = render(
      <MessageList messages={messages} thoughtMessages={thoughtMessages} />,
    );

    const userBubble = container.querySelector('[data-message-role="user"]');
    const thoughtBubble = container.querySelector(
      '[data-message-category="thought"]',
    );

    expect(userBubble).toBeTruthy();
    expect(thoughtBubble).toBeTruthy();
    expect(userBubble?.className).toContain("bg-slate-600");
    expect(thoughtBubble?.className).toContain("bg-amber-50");
  });
});
