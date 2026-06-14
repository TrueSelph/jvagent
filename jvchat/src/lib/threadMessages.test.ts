import { describe, it, expect } from "vitest";

import { buildThreadMessages } from "./threadMessages";
import type { Message } from "../types/message";

function assistantText(
  id: string,
  content: string,
  interactionId = "ix-1",
): Message {
  return {
    id,
    role: "assistant",
    content,
    timestamp: "2026-01-01T00:00:00.000Z",
    interactionId,
  };
}

function assistantThought(
  id: string,
  content: string,
  interactionId = "ix-1",
): Message {
  return {
    id,
    role: "assistant",
    content,
    timestamp: "2026-01-01T00:00:00.000Z",
    interactionId,
    category: "thought",
    thoughtType: "reasoning",
  };
}

function userMessage(id: string, content: string): Message {
  return {
    id,
    role: "user",
    content,
    timestamp: "2026-01-01T00:00:00.000Z",
  };
}

function textParts(thread: ReturnType<typeof buildThreadMessages>[number]) {
  return (thread.content as Array<{ type: string; text?: string }>).filter(
    (p) => p.type === "text",
  );
}

function assistantToolCall(
  id: string,
  toolName: string,
  interactionId = "ix-1",
  opts?: { result?: string; segmentId?: string },
): Message {
  const segmentId = opts?.segmentId ?? id;
  const hasResult = opts?.result !== undefined;
  return {
    id,
    role: "assistant",
    content: toolName,
    timestamp: "2026-01-01T00:00:00.000Z",
    interactionId,
    category: "thought",
    thoughtType: hasResult ? "tool_result" : "tool_call",
    segmentId,
    metadata: hasResult
      ? { tool_name: toolName, tool_result: opts?.result }
      : { tool_name: toolName, tool_args: {} },
  };
}

function partTypes(thread: ReturnType<typeof buildThreadMessages>[number]) {
  return (thread.content as unknown as Array<{ type: string }>).map(
    (p) => p.type,
  );
}

describe("buildThreadMessages", () => {
  it("keeps all reasoning and tools on the first row when interleaved with catalog cards", () => {
    const messages: Message[] = [
      userMessage("u1", "drills"),
      assistantThought("t1", "Searching catalog…"),
      assistantToolCall("tc1", "lightspeed__search_products", "ix-1", {
        segmentId: "seg1",
      }),
      assistantToolCall("tr1", "lightspeed__search_products", "ix-1", {
        segmentId: "seg1",
        result: "[]",
      }),
      assistantText("c1", "Card one"),
      assistantToolCall("tc2", "lightspeed__emit_catalog_message", "ix-1", {
        segmentId: "seg2",
      }),
      assistantToolCall("tr2", "lightspeed__emit_catalog_message", "ix-1", {
        segmentId: "seg2",
        result: "ok",
      }),
      assistantText("c2", "Card two"),
      assistantText("closer", "Compare?"),
    ];

    const thread = buildThreadMessages(messages);
    const assistant = thread.filter((m) => m.role === "assistant");

    expect(assistant).toHaveLength(3);
    expect(partTypes(assistant[0])).toEqual([
      "reasoning",
      "tool-call",
      "tool-call",
      "text",
    ]);
    expect(partTypes(assistant[1])).toEqual(["text"]);
    expect(partTypes(assistant[2])).toEqual(["text"]);
  });

  it("splits multi-adhoc catalog turn into separate assistant thread rows", () => {
    const messages: Message[] = [
      userMessage("u1", "show me drills"),
      assistantThought("t1", "Searching catalog…"),
      assistantText("c1", "**Drill A**\n\n[View Details](https://example/a)"),
      assistantText("c2", "**Drill B**\n\n[View Details](https://example/b)"),
      assistantText(
        "closer",
        "Would you like to compare these options?",
      ),
    ];

    const thread = buildThreadMessages(messages);
    const assistant = thread.filter((m) => m.role === "assistant");

    expect(assistant).toHaveLength(3);
    expect(textParts(assistant[0])).toHaveLength(1);
    expect(textParts(assistant[0])[0]?.text).toContain("Drill A");
    expect(
      (assistant[0].content as unknown as Array<{ type: string }>).some(
        (p) => p.type === "reasoning",
      ),
    ).toBe(true);
    expect(textParts(assistant[1])).toEqual([
      { type: "text", text: expect.stringContaining("Drill B") },
    ]);
    expect(textParts(assistant[2])).toEqual([
      {
        type: "text",
        text: "Would you like to compare these options?",
      },
    ]);
    expect(assistant[0].id).toBe("c1");
    expect(assistant[1].id).toBe("c2");
    expect(assistant[2].id).toBe("closer");
  });

  it("never emits duplicate thread message ids", () => {
    const messages: Message[] = [
      userMessage("u1", "show me drills"),
      assistantThought("t1", "Searching catalog…"),
      assistantText("c1", "Card one"),
      assistantText("c2", "Card two"),
      assistantText("closer", "Compare?"),
    ];

    const ids = buildThreadMessages(messages).map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("avoids duplicate turn ids across conversation turns", () => {
    const messages: Message[] = [
      userMessage("u1", "q1"),
      assistantText("a1", "answer 1", "ix-shared"),
      userMessage("u2", "q2"),
      assistantText("a2", "answer 2", "ix-shared"),
    ];

    const ids = buildThreadMessages(messages).map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toEqual(["u1", "a1", "u2", "a2"]);
  });

  it("keeps a single-answer turn as one assistant message", () => {
    const messages: Message[] = [
      userMessage("u1", "hello"),
      assistantThought("t1", "Thinking…"),
      assistantText("a1", "Hello! How can I help?"),
    ];

    const thread = buildThreadMessages(messages);
    const assistant = thread.filter((m) => m.role === "assistant");

    expect(assistant).toHaveLength(1);
    expect(textParts(assistant[0])).toHaveLength(1);
    expect(textParts(assistant[0])[0]?.text).toBe("Hello! How can I help?");
  });

  it("splits text-only multi-adhoc rows without a thought prefix", () => {
    const messages: Message[] = [
      userMessage("u1", "catalog"),
      assistantText("c1", "Card one"),
      assistantText("c2", "Card two"),
    ];

    const thread = buildThreadMessages(messages);
    const assistant = thread.filter((m) => m.role === "assistant");

    expect(assistant).toHaveLength(2);
    expect(textParts(assistant[0])).toEqual([{ type: "text", text: "Card one" }]);
    expect(textParts(assistant[1])).toEqual([{ type: "text", text: "Card two" }]);
  });
});
