import { describe, expect, it } from "vitest";
import { messagesForPersistence } from "../utils/storage";
import { buildThreadMessages } from "./threadMessages";
import { messageObservability } from "./messageObservability";
import type { Message } from "../types/message";

describe("persisted usage receipts", () => {
  it("survives JSON history reload without persisting debug content", () => {
    const original: Message = {
      id: "answer", role: "assistant", content: "Result", timestamp: "2026-11-04T00:00:00Z",
      debugData: { private: "SECRET-PROMPT", interaction: {
        utterance: "PRIVATE-INPUT", usage: { total_tokens: 123, estimated_cost_usd: 0.004, unknown_cost_call_count: 1 },
        observability_metrics: [{ event_type: "model_call", data: {
          model: "gpt-4.1", prompt: "SECRET-PROMPT", usage: { prompt_tokens: 100, completion_tokens: 23 },
        } }, { event_type: "tool_result", data: { content: "PRIVATE-TOOL-RESULT" } }],
      } },
    };
    const serialized = JSON.stringify(messagesForPersistence([original] as unknown as Record<string, unknown>[]));
    expect(serialized).not.toMatch(/SECRET|PRIVATE|debugData|tool_result/);
    const loaded = JSON.parse(serialized);
    const thread = buildThreadMessages(loaded, false);
    const custom = thread[0].metadata?.custom;
    expect(custom?.observability).toMatchObject({ usage: { total_tokens: 123, estimated_cost_usd: 0.004, unknown_cost_call_count: 1 } });
    expect(custom?.debugMessage).toBeNull();
    expect(original.debugData.private).toBe("SECRET-PROMPT");
  });

  it("bounds detail rows without changing aggregate totals", () => {
    const result = messageObservability({ debugData: { interaction: {
      usage: { total_tokens: 20000 },
      observability_metrics: Array.from({ length: 150 }, () => ({ event_type: "model_call", data: { model: "x".repeat(1000) } })),
    } } });
    expect(result?.observability_metrics).toHaveLength(128);
    expect(result?.observability_metrics[0].data.model).toHaveLength(128);
    expect(result?.metricsTruncated).toBe(true);
    expect(result?.usage.total_tokens).toBe(20000);
  });

  it("rejects malformed numeric receipts and unknown persisted fields", () => {
    expect(messageObservability({ observability: {
      usage: { total_tokens: -1, estimated_cost_usd: Infinity, prompt_tokens: "100", secret: "SECRET" },
      observability_metrics: [{ event_type: "tool_call", data: { secret: "SECRET" } }],
    } })).toBeUndefined();
  });
});
