import { describe, expect, it } from "vitest";
import {
  answerText,
  emptyTurn,
  hasAnswer,
  humanizeToolName,
  reduceMessage,
  withError,
} from "./reducer";
import type { ResponseMessageData } from "./types";

const msg = (over: Partial<ResponseMessageData>): ResponseMessageData => ({
  message_type: "stream_chunk",
  content: "",
  category: "user",
  ...over,
});

describe("answer accumulation", () => {
  it("concatenates chunks within one segment", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ content: "Hello ", segment_id: "a" }));
    s = reduceMessage(s, msg({ content: "world", segment_id: "a" }));
    expect(answerText(s)).toBe("Hello world");
    expect(s.segments).toHaveLength(1);
  });

  it("keeps distinct segment_ids as separate blocks", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ content: "Heads up.", segment_id: "a" }));
    s = reduceMessage(s, msg({ content: "Your total is $49.", segment_id: "b" }));
    expect(s.segments.map((x) => x.id)).toEqual(["a", "b"]);
    // Separated, not run together — the bug this fixes.
    expect(answerText(s)).toBe("Heads up.\n\nYour total is $49.");
  });

  it("treats missing segment_id as one default segment", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ content: "a" }));
    s = reduceMessage(s, msg({ content: "b" }));
    expect(s.segments).toHaveLength(1);
    expect(answerText(s)).toBe("ab");
  });

  it("uses final only when nothing streamed", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ message_type: "final", content: "Whole answer" }));
    expect(answerText(s)).toBe("Whole answer");
  });

  it("ignores final when chunks already arrived (no duplication)", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ content: "streamed" }));
    s = reduceMessage(s, msg({ message_type: "final", content: "streamed" }));
    expect(answerText(s)).toBe("streamed");
  });

  it("accumulates adhoc messages as visible answer", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ message_type: "adhoc", content: "canned" }));
    expect(hasAnswer(s)).toBe(true);
  });
});

describe("thought routing", () => {
  it("accumulates reasoning separately from the answer", () => {
    let s = emptyTurn();
    s = reduceMessage(
      s,
      msg({ category: "thought", thought_type: "reasoning", content: "thinking" })
    );
    expect(s.reasoning).toBe("thinking");
    expect(answerText(s)).toBe("");
  });

  it("turns tool_call into running activity and tool_result into done", () => {
    let s = emptyTurn();
    s = reduceMessage(
      s,
      msg({
        category: "thought",
        thought_type: "tool_call",
        segment_id: "t1",
        metadata: { tool_name: "storefront__search_products" },
      })
    );
    expect(s.activity).toEqual([
      { id: "t1", label: "search products", status: "running" },
    ]);

    s = reduceMessage(
      s,
      msg({
        category: "thought",
        thought_type: "tool_result",
        segment_id: "t1",
        metadata: { tool_name: "storefront__search_products" },
      })
    );
    expect(s.activity[0].status).toBe("done");
    expect(s.activity).toHaveLength(1); // paired, not duplicated
  });

  it("marks a failed tool result as error", () => {
    let s = emptyTurn();
    s = reduceMessage(
      s,
      msg({
        category: "thought",
        thought_type: "tool_result",
        segment_id: "t9",
        metadata: { tool_name: "x__y", is_error: true },
      })
    );
    expect(s.activity[0].status).toBe("error");
  });

  it("keeps thoughts out of the visible answer entirely", () => {
    let s = emptyTurn();
    s = reduceMessage(
      s,
      msg({ category: "thought", thought_type: "status", content: "Working…" })
    );
    expect(answerText(s)).toBe("");
    expect(s.activity[0].label).toBe("Working…");
  });
});

describe("suggestions", () => {
  it("picks up suggestions from any message and lets the latest win", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ metadata: { suggestions: ["A", "B"] } }));
    expect(s.suggestions.map((x) => x.label)).toEqual(["A", "B"]);
    s = reduceMessage(s, msg({ metadata: { suggestions: ["C"] } }));
    expect(s.suggestions.map((x) => x.label)).toEqual(["C"]);
  });

  it("does not clear suggestions on later messages without any", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ metadata: { suggestions: ["A"] } }));
    s = reduceMessage(s, msg({ content: "more text" }));
    expect(s.suggestions.map((x) => x.label)).toEqual(["A"]);
  });
});

describe("errors", () => {
  it("records the error without polluting the answer", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ content: "partial" }));
    s = withError(s, "Connection lost");
    expect(s.error).toBe("Connection lost");
    expect(answerText(s)).toBe("partial");
  });

  it("stops any running activity", () => {
    let s = emptyTurn();
    s = reduceMessage(
      s,
      msg({
        category: "thought",
        thought_type: "tool_call",
        segment_id: "t1",
        metadata: { tool_name: "a__b" },
      })
    );
    s = withError(s, "boom");
    expect(s.activity[0].status).toBe("error");
  });
});

describe("humanizeToolName", () => {
  it("strips the action namespace and underscores", () => {
    expect(humanizeToolName("storefront__search_products")).toBe("search products");
    expect(humanizeToolName("reply")).toBe("reply");
    expect(humanizeToolName("")).toBe("");
  });
});
