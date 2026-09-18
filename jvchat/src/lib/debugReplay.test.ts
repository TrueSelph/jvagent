import { describe, expect, it } from "vitest";

import {
  buildExportV2,
  buildQueryPayload,
  buildReplaySnapshot,
  EXPORT_VERSION,
  formatCopyPrompt,
  normalizeLiteLLMModelId,
  parseImportFile,
  parseJsonArray,
  unwrapQueryActionResponse,
} from "./debugReplay";

const tools = [
  {
    type: "function",
    function: {
      name: "reply",
      description: "Send a reply",
      parameters: { type: "object", properties: { text: { type: "string" } } },
    },
  },
];

const replay = [
  {
    role: "assistant",
    content: "",
    tool_calls: [
      {
        id: "call_1",
        type: "function",
        function: { name: "lookup", arguments: "{}" },
      },
    ],
  },
  { role: "tool", tool_call_id: "call_1", name: "lookup", content: "ok" },
];

const toolCalls = [
  {
    id: "call_reply",
    type: "function",
    function: { name: "reply", arguments: '{"text":"hi"}' },
  },
];

function snapshot() {
  const built = buildReplaySnapshot({
    user: "what is the status?",
    system: "you are helpful",
    historyText: JSON.stringify([{ role: "user", content: "hi" }]),
    replayText: JSON.stringify(replay),
    toolsText: JSON.stringify(tools),
    model: "openai/gpt-4.1",
    provider: "openai",
    response: "",
    toolCalls,
    finishReason: "tool_calls",
    calledBy: "OrchestratorInteractAction",
    toolSource: "recorded",
    temperature: 0.2,
    maxTokens: 1024,
  });
  if (!built.ok) throw new Error(built.error);
  return built.snapshot;
}

describe("parseJsonArray", () => {
  it("treats empty as []", () => {
    expect(parseJsonArray("", "History (JSON)")).toEqual({ ok: true, value: [] });
  });

  it("rejects non-arrays", () => {
    expect(parseJsonArray("{}", "Tools (JSON)")).toEqual({
      ok: false,
      error: "Cannot retest: Tools (JSON) must be an array.",
    });
  });

  it("rejects invalid JSON", () => {
    expect(parseJsonArray("[", "History (JSON)")).toEqual({
      ok: false,
      error: "Cannot retest: History (JSON) is not valid JSON.",
    });
  });
});

describe("formatCopyPrompt", () => {
  it("includes response, tool_calls, tools, and replay", () => {
    const text = formatCopyPrompt(snapshot());
    expect(text).toContain("Model: openai/gpt-4.1");
    expect(text).toContain("Provider: openai");
    expect(text).toContain("you are helpful");
    expect(text).toContain("what is the status?");
    expect(text).toContain("This-turn tool replay:");
    expect(text).toContain("call_1");
    expect(text).toContain("lookup");
    expect(text).toContain('"name": "reply"');
    expect(text).toContain("Expected result:");
    expect(text).toContain("Response:");
    expect(text).toContain("tool_calls:");
    expect(text).toContain("call_reply");
    expect(text).toContain("finish_reason: tool_calls");
    expect(text).not.toContain("Improvement Instruction:");
  });

  it("appends improve instruction when provided", () => {
    const text = formatCopyPrompt(snapshot(), "make it shorter");
    expect(text).toContain("Improvement Instruction:");
    expect(text).toContain("make it shorter");
    expect(text).toContain("Return a raw markdown.");
  });
});

describe("buildQueryPayload", () => {
  it("sends messages + tools even when the original tick finished with text", () => {
    const built = buildReplaySnapshot({
      user: "thanks",
      system: "sys",
      historyText: "[]",
      replayText: JSON.stringify(replay),
      toolsText: JSON.stringify(tools),
      model: "openai/gpt-4.1",
      provider: "openai",
      response: "all done",
      toolCalls: [],
      finishReason: "stop",
      toolSource: "sibling",
    });
    if (!built.ok) throw new Error(built.error);
    const payload = buildQueryPayload(built.snapshot);
    expect(payload.model).toBe("openai/gpt-4.1");
    expect(payload.provider).toBe("openai");
    expect(payload.tools).toEqual(tools);
    expect(payload.tool_choice).toBe("auto");
    expect(payload.parallel_tool_calls).toBe(false);
    expect(payload.messages).toEqual([
      { role: "system", content: "sys" },
      { role: "user", content: "thanks" },
      ...replay,
    ]);
  });

  it("omits tools and tool_choice when the editor has none", () => {
    const built = buildReplaySnapshot({
      user: "hi",
      system: "",
      historyText: "[]",
      replayText: "[]",
      toolsText: "[]",
      model: "gpt-4o",
      provider: "",
    });
    if (!built.ok) throw new Error(built.error);
    const payload = buildQueryPayload(built.snapshot);
    expect(payload.tools).toBeUndefined();
    expect(payload.tool_choice).toBeUndefined();
    expect(payload.messages).toEqual([{ role: "user", content: "hi" }]);
  });

  it("forwards recorded temperature and max_tokens", () => {
    const payload = buildQueryPayload(snapshot());
    expect(payload.temperature).toBe(0.2);
    expect(payload.max_tokens).toBe(1024);
  });

  it("lowercases a wrong-cased LiteLLM provider prefix", () => {
    const built = buildReplaySnapshot({
      user: "hi",
      system: "",
      historyText: "[]",
      replayText: "[]",
      toolsText: "[]",
      model: "Openai/gpt-4.1",
      provider: "litellm",
    });
    if (!built.ok) throw new Error(built.error);
    expect(buildQueryPayload(built.snapshot).model).toBe("openai/gpt-4.1");
  });

  it("leaves already-correct and bare model ids unchanged", () => {
    expect(normalizeLiteLLMModelId("openai/gpt-4.1")).toBe("openai/gpt-4.1");
    expect(normalizeLiteLLMModelId("OpenAI/gpt-4.1")).toBe("openai/gpt-4.1");
    expect(normalizeLiteLLMModelId("gpt-4.1")).toBe("gpt-4.1");
    expect(
      normalizeLiteLLMModelId("OpenRouter/anthropic/claude-sonnet-4-5"),
    ).toBe("openrouter/anthropic/claude-sonnet-4-5");
  });
});

describe("export/import v2", () => {
  it("round-trips live editor fields", () => {
    const selection = {
      user_prompt: "edited user",
      system_prompt: "edited system",
      historyText: '[{"role":"user","content":"x"}]',
      replayText: JSON.stringify(replay),
      toolsText: JSON.stringify(tools),
      replayModel: "openai/gpt-4.1",
      provider: "openai",
      testResult: { success: true, response: "ok" },
    };
    const exported = buildExportV2({
      parentInteractions: [{ id: "p1", metrics: [{}] }],
      pagination: { page: 1 },
      selectedParentIndex: 0,
      selectedMetricIndex: 1,
      selection,
      agentId: "agent-1",
      exportedAt: "2026-01-01T00:00:00.000Z",
    });
    expect(exported.version).toBe(EXPORT_VERSION);

    const parsed = parseImportFile(JSON.parse(JSON.stringify(exported)));
    expect(parsed.kind).toBe("v2");
    if (parsed.kind !== "v2") return;
    expect(parsed.selectedParentIndex).toBe(0);
    expect(parsed.selectedMetricIndex).toBe(1);
    expect(parsed.selection).toEqual(selection);
    expect(parsed.parentInteractions).toHaveLength(1);
  });

  it("reads v1 parents-only exports", () => {
    const parsed = parseImportFile({
      parentInteractions: [{ id: "a" }],
      selectedParentIndex: 0,
      selectedMetricIndex: 2,
      pagination: null,
    });
    expect(parsed).toMatchObject({
      kind: "v1",
      selectedParentIndex: 0,
      selectedMetricIndex: 2,
    });
  });

  it("reads legacy single-interaction exports without wiping the shape", () => {
    const parsed = parseImportFile({
      interaction: { data: { user_prompt: "hi" } },
      testResult: { success: true },
    });
    expect(parsed.kind).toBe("legacy");
    if (parsed.kind !== "legacy") return;
    expect(parsed.interaction).toEqual({ data: { user_prompt: "hi" } });
    expect(parsed.testResult).toEqual({ success: true });
  });
});

describe("unwrapQueryActionResponse", () => {
  it("unwraps success/data envelopes", () => {
    expect(
      unwrapQueryActionResponse({
        success: true,
        data: { response: "hi", tool_calls: [] },
      }),
    ).toEqual({ response: "hi", tool_calls: [] });
  });

  it("passes through raw to_dict payloads", () => {
    expect(unwrapQueryActionResponse({ response: "hi", tool_calls: [] })).toEqual({
      response: "hi",
      tool_calls: [],
    });
  });
});
