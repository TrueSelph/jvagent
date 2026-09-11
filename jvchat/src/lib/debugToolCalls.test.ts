import { describe, expect, it } from "vitest";

import {
  enrichToolCallArgs,
  observationReplayForMetric,
  pairAgentTraceToolCalls,
  resolveRetestTools,
  toolCallsForMetric,
} from "./debugToolCalls";

describe("pairAgentTraceToolCalls", () => {
  it("returns empty for missing or non-array traces", () => {
    expect(pairAgentTraceToolCalls(undefined)).toEqual([]);
    expect(pairAgentTraceToolCalls(null)).toEqual([]);
    expect(pairAgentTraceToolCalls({})).toEqual([]);
  });

  it("pairs tool_call and tool_result by segment_id", () => {
    const tools = pairAgentTraceToolCalls([
      {
        thought_type: "reasoning",
        segment_id: "r1",
        content: "thinking",
      },
      {
        thought_type: "tool_call",
        segment_id: "seg1",
        content: "pageindex__search",
      },
      {
        thought_type: "tool_result",
        segment_id: "seg1",
        content: "No matching documents found.",
      },
    ]);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({
      segmentId: "seg1",
      toolName: "pageindex__search",
      result: "No matching documents found.",
      isError: false,
      args: {},
    });
  });

  it("keeps an unpaired call and flags error results", () => {
    const tools = pairAgentTraceToolCalls([
      {
        thought_type: "tool_call",
        segment_id: "a",
        content: "web__fetch",
      },
      {
        thought_type: "tool_result",
        segment_id: "b",
        content: "(tool error: timeout)",
      },
    ]);
    expect(tools).toHaveLength(2);
    expect(tools[0].result).toBeUndefined();
    expect(tools[1]).toMatchObject({
      toolName: "tool",
      result: "(tool error: timeout)",
      isError: true,
    });
  });
});

describe("enrichToolCallArgs", () => {
  it("fills args from model_call tool_calls matching by name in order", () => {
    const paired = pairAgentTraceToolCalls([
      {
        thought_type: "tool_call",
        segment_id: "seg1",
        content: "pageindex__search",
      },
      {
        thought_type: "tool_result",
        segment_id: "seg1",
        content: "No matching documents found.",
      },
    ]);
    const enriched = enrichToolCallArgs(paired, [
      {
        event_type: "model_call",
        data: {
          tool_calls: [
            {
              type: "function",
              function: {
                name: "pageindex__search",
                arguments: '{"query":"opening hours"}',
              },
            },
          ],
        },
      },
    ]);
    expect(enriched[0].args).toEqual({ query: "opening hours" });
  });

  it("matches repeated tool names in call order", () => {
    const tools = pairAgentTraceToolCalls([
      { thought_type: "tool_call", segment_id: "1", content: "search" },
      { thought_type: "tool_result", segment_id: "1", content: "a" },
      { thought_type: "tool_call", segment_id: "2", content: "search" },
      { thought_type: "tool_result", segment_id: "2", content: "b" },
    ]);
    const enriched = enrichToolCallArgs(tools, [
      {
        data: {
          tool_calls: [
            { function: { name: "search", arguments: { q: "first" } } },
            { function: { name: "search", arguments: { q: "second" } } },
          ],
        },
      },
    ]);
    expect(enriched[0].args).toEqual({ q: "first" });
    expect(enriched[1].args).toEqual({ q: "second" });
  });
});

describe("toolCallsForMetric", () => {
  const trace = [
    {
      thought_type: "tool_call",
      segment_id: "toolcall-abc",
      content: "pageindex__search",
    },
    {
      thought_type: "tool_result",
      segment_id: "toolcall-abc",
      content: "No matching documents found.",
    },
  ];
  const metrics = [
    { event_type: "helm_shift", data: { from_helm: "", to_helm: "orch" } },
    {
      event_type: "model_call",
      data: {
        tool_calls: [
          {
            function: {
              name: "pageindex__search",
              arguments: '{"query": "opening hours"}',
            },
          },
        ],
      },
    },
    {
      event_type: "model_call",
      data: {
        response:
          "I couldn't find information about opening hours in the current resources.",
      },
    },
  ];

  it("attaches the tool result to the model_call that requested the tool", () => {
    expect(toolCallsForMetric(trace, metrics, 1)).toEqual([
      {
        segmentId: "toolcall-abc",
        toolName: "pageindex__search",
        args: { query: "opening hours" },
        result: "No matching documents found.",
        isError: false,
      },
    ]);
  });

  it("does not show tools on a later text-response model_call", () => {
    expect(toolCallsForMetric(trace, metrics, 2)).toEqual([]);
  });

  it("does not show tools on non-model_call metrics", () => {
    expect(toolCallsForMetric(trace, metrics, 0)).toEqual([]);
  });

  it("partitions sequential tool-call ticks", () => {
    const twoTrace = [
      { thought_type: "tool_call", segment_id: "a", content: "search" },
      { thought_type: "tool_result", segment_id: "a", content: "first" },
      { thought_type: "tool_call", segment_id: "b", content: "search" },
      { thought_type: "tool_result", segment_id: "b", content: "second" },
    ];
    const twoMetrics = [
      {
        data: {
          tool_calls: [{ function: { name: "search", arguments: { q: "1" } } }],
        },
      },
      {
        data: {
          tool_calls: [{ function: { name: "search", arguments: { q: "2" } } }],
        },
      },
    ];
    expect(toolCallsForMetric(twoTrace, twoMetrics, 0)[0].result).toBe("first");
    expect(toolCallsForMetric(twoTrace, twoMetrics, 1)[0].result).toBe("second");
  });
});

describe("observationReplayForMetric", () => {
  const lookupCall = {
    id: "call_lookup_1",
    type: "function",
    function: {
      name: "lookup_report__get_issue",
      arguments: '{"reference_number":"R381235"}',
    },
  };
  const replyCall = {
    id: "call_reply_1",
    type: "function",
    function: {
      name: "reply",
      arguments: '{"text":"Here are the details for report R381235"}',
    },
  };
  const trace = [
    {
      thought_type: "tool_call",
      segment_id: "seg-lookup",
      content: "lookup_report__get_issue",
    },
    {
      thought_type: "tool_result",
      segment_id: "seg-lookup",
      content: "Title: damaged road. Status: open.",
    },
    {
      thought_type: "tool_call",
      segment_id: "seg-reply",
      content: "reply",
    },
    {
      thought_type: "tool_result",
      segment_id: "seg-reply",
      content: "ok",
    },
  ];
  const metrics = [
    { event_type: "model_call", data: { tool_calls: [lookupCall] } },
    { event_type: "model_call", data: { tool_calls: [replyCall] } },
  ];

  it("returns empty for the first tick", () => {
    expect(observationReplayForMetric(trace, metrics, 0)).toEqual([]);
  });

  it("replays earlier lookup when retesting the reply tick", () => {
    expect(observationReplayForMetric(trace, metrics, 1)).toEqual([
      {
        role: "assistant",
        content: "",
        tool_calls: [lookupCall],
      },
      {
        role: "tool",
        tool_call_id: "call_lookup_1",
        name: "lookup_report__get_issue",
        content: "Title: damaged road. Status: open.",
      },
    ]);
  });

  it("omits the selected tick's own tool_calls", () => {
    const replay = observationReplayForMetric(trace, metrics, 1);
    expect(JSON.stringify(replay)).not.toContain("call_reply_1");
    expect(JSON.stringify(replay)).not.toContain('"name":"reply"');
  });
});

describe("resolveRetestTools", () => {
  const recordedTools = [
    {
      type: "function",
      function: {
        name: "reply",
        description: "Send a reply",
        parameters: { type: "object", properties: { text: { type: "string" } } },
      },
    },
  ];
  const siblingTools = [
    {
      type: "function",
      function: {
        name: "reply",
        description: "Sibling schema",
        parameters: { type: "object", properties: {} },
      },
    },
    {
      type: "function",
      function: {
        name: "update_plan",
        description: "Update the plan",
        parameters: { type: "object", properties: {} },
      },
    },
  ];

  it("uses recorded tools when present", () => {
    expect(
      resolveRetestTools({
        tools: recordedTools,
        toolNames: ["reply", "update_plan"],
        siblingMetrics: [
          {
            event_type: "model_call",
            data: {
              tools: siblingTools,
              tool_names: ["reply", "update_plan"],
            },
          },
        ],
      }),
    ).toEqual({ tools: recordedTools, source: "recorded" });
  });

  it("inherits sibling tools whose tool_names match", () => {
    expect(
      resolveRetestTools({
        tools: [],
        toolNames: ["reply", "update_plan"],
        siblingMetrics: [
          { event_type: "helm_shift", data: { tools: recordedTools } },
          {
            event_type: "model_call",
            data: {
              tools: siblingTools,
              tool_names: ["reply", "update_plan"],
            },
          },
        ],
      }),
    ).toEqual({ tools: siblingTools, source: "sibling" });
  });

  it("stubs from tool_names when no schemas exist", () => {
    expect(
      resolveRetestTools({
        tools: [],
        toolNames: ["reply", "update_plan"],
        siblingMetrics: [{ event_type: "model_call", data: {} }],
      }),
    ).toEqual({
      source: "stub",
      tools: [
        {
          type: "function",
          function: {
            name: "reply",
            description: "",
            parameters: { type: "object", properties: {} },
          },
        },
        {
          type: "function",
          function: {
            name: "update_plan",
            description: "",
            parameters: { type: "object", properties: {} },
          },
        },
      ],
    });
  });

  it("stubs from tool_calls names when tool_names is missing", () => {
    expect(
      resolveRetestTools({
        tools: [],
        toolCalls: [
          { function: { name: "pageindex__search", arguments: "{}" } },
          { function: { name: "pageindex__search", arguments: "{}" } },
        ],
      }),
    ).toEqual({
      source: "stub",
      tools: [
        {
          type: "function",
          function: {
            name: "pageindex__search",
            description: "",
            parameters: { type: "object", properties: {} },
          },
        },
      ],
    });
  });

  it("returns none when tools were used and nothing can be sent", () => {
    expect(
      resolveRetestTools({
        tools: [],
        toolNames: [],
        toolCalls: [],
        siblingMetrics: [{ event_type: "model_call", data: {} }],
      }),
    ).toEqual({ tools: [], source: "none" });
  });
});
