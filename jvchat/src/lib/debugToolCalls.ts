/** Pair agent_trace tool thoughts and recover args from model_call metrics. */

export type DebugToolCall = {
  segmentId: string;
  toolName: string;
  args: Record<string, unknown>;
  result?: unknown;
  isError: boolean;
};

type AgentTraceEntry = {
  thought_type?: string;
  segment_id?: string;
  content?: unknown;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseArgs(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  if (typeof raw === "string") {
    const text = raw.trim();
    if (!text) return {};
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}

function isErrorResult(result: unknown): boolean {
  return typeof result === "string" && result.startsWith("(tool error");
}

/** Fold tool_call + tool_result entries that share a segment_id into one card. */
export function pairAgentTraceToolCalls(trace: unknown): DebugToolCall[] {
  if (!Array.isArray(trace)) return [];
  const tools: DebugToolCall[] = [];
  const bySegment = new Map<string, number>();

  for (const raw of trace) {
    const entry = asRecord(raw) as AgentTraceEntry | null;
    if (!entry) continue;
    const thoughtType = String(entry.thought_type || "");
    if (thoughtType !== "tool_call" && thoughtType !== "tool_result") continue;

    const segmentId = String(entry.segment_id || "");
    if (thoughtType === "tool_call") {
      const toolName = String(entry.content || "tool");
      const key = segmentId || `call-${tools.length}`;
      bySegment.set(key, tools.length);
      tools.push({
        segmentId: key,
        toolName,
        args: {},
        isError: false,
      });
      continue;
    }

    const result = entry.content;
    const key = segmentId || `result-${tools.length}`;
    const idx = bySegment.get(key);
    if (idx !== undefined && tools[idx]) {
      tools[idx] = {
        ...tools[idx],
        result,
        isError: isErrorResult(result),
      };
    } else {
      tools.push({
        segmentId: key,
        toolName: "tool",
        args: {},
        result,
        isError: isErrorResult(result),
      });
    }
  }
  return tools;
}

function collectMetricToolCalls(
  metrics: unknown,
): Array<{ name: string; args: Record<string, unknown> }> {
  if (!Array.isArray(metrics)) return [];
  const out: Array<{ name: string; args: Record<string, unknown> }> = [];
  for (const metric of metrics) {
    const rec = asRecord(metric);
    if (!rec) continue;
    const data = asRecord(rec.data) || rec;
    const calls = data.tool_calls;
    if (!Array.isArray(calls)) continue;
    for (const call of calls) {
      const tc = asRecord(call);
      if (!tc) continue;
      const fn = asRecord(tc.function);
      const name = String(fn?.name ?? tc.name ?? "");
      if (!name) continue;
      out.push({
        name,
        args: parseArgs(fn?.arguments ?? tc.arguments),
      });
    }
  }
  return out;
}

function toolCallsOnMetric(metric: unknown): Array<{
  name: string;
  args: Record<string, unknown>;
}> {
  return collectMetricToolCalls([metric]);
}

function takeMatchingTrace(
  queue: DebugToolCall[],
  name: string,
): DebugToolCall | undefined {
  const idx = queue.findIndex((t) => t.toolName === name);
  if (idx < 0) return undefined;
  return queue.splice(idx, 1)[0];
}

/** Fill empty args from model_call.data.tool_calls, matching by tool name in order. */
export function enrichToolCallArgs(
  tools: DebugToolCall[],
  metrics: unknown,
): DebugToolCall[] {
  if (tools.length === 0) return tools;
  const queue = collectMetricToolCalls(metrics);
  if (queue.length === 0) return tools;
  const used = new Set<number>();
  return tools.map((tool) => {
    if (Object.keys(tool.args).length > 0) return tool;
    const idx = queue.findIndex((q, i) => !used.has(i) && q.name === tool.toolName);
    if (idx < 0) return tool;
    used.add(idx);
    return { ...tool, args: queue[idx].args };
  });
}

/**
 * Tools for one observability metric. A text-response model_call has no
 * tool_calls; the earlier tick that requested the tool owns the result.
 */
export function toolCallsForMetric(
  agentTrace: unknown,
  metrics: unknown,
  metricIndex: number,
): DebugToolCall[] {
  if (!Array.isArray(metrics) || metricIndex < 0 || metricIndex >= metrics.length) {
    return [];
  }
  const queue = pairAgentTraceToolCalls(agentTrace);
  let assigned: DebugToolCall[] = [];
  for (let i = 0; i < metrics.length; i++) {
    const calls = toolCallsOnMetric(metrics[i]);
    const slice: DebugToolCall[] = [];
    for (const call of calls) {
      const matched = takeMatchingTrace(queue, call.name);
      slice.push({
        segmentId: matched?.segmentId || `metric-${i}-${slice.length}`,
        toolName: call.name,
        args:
          Object.keys(call.args).length > 0
            ? call.args
            : matched?.args || {},
        result: matched?.result,
        isError: matched?.isError ?? false,
      });
    }
    if (i === metricIndex) assigned = slice;
  }
  return assigned;
}

export type ObservationReplayMessage = {
  role: "assistant" | "tool";
  content: string;
  tool_calls?: unknown[];
  tool_call_id?: string;
  name?: string;
};

function rawToolCallsOnMetric(metric: unknown): unknown[] {
  const data = metricPayload(metric);
  if (!data) return [];
  return Array.isArray(data.tool_calls) ? data.tool_calls : [];
}

function toolCallId(call: unknown, fallback: string): string {
  const rec = asRecord(call);
  if (typeof rec?.id === "string" && rec.id) return rec.id;
  return fallback;
}

function toolCallName(call: unknown, fallback = ""): string {
  const rec = asRecord(call);
  const fn = rec ? asRecord(rec.function) : null;
  return String(fn?.name ?? rec?.name ?? fallback);
}

function resultContent(result: unknown): string {
  if (result === undefined || result === null) return "";
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result);
  } catch {
    return String(result);
  }
}

/**
 * Native-protocol observation replay for retesting a later tick.
 *
 * Earlier model_call tool_calls (index < metricIndex) become assistant
 * + tool messages after the user prompt. The selected tick's own calls
 * are the output to reproduce, so they are omitted.
 */
export function observationReplayForMetric(
  agentTrace: unknown,
  metrics: unknown,
  metricIndex: number,
): ObservationReplayMessage[] {
  if (!Array.isArray(metrics) || metricIndex <= 0) return [];
  const messages: ObservationReplayMessage[] = [];
  const limit = Math.min(metricIndex, metrics.length);
  for (let i = 0; i < limit; i++) {
    const rawCalls = rawToolCallsOnMetric(metrics[i]);
    if (rawCalls.length === 0) continue;
    const paired = toolCallsForMetric(agentTrace, metrics, i);
    messages.push({
      role: "assistant",
      content: "",
      tool_calls: rawCalls,
    });
    rawCalls.forEach((call, idx) => {
      const fallbackId = `metric-${i}-${idx}`;
      const pairedCall = paired[idx];
      const name = toolCallName(call, pairedCall?.toolName || "");
      const msg: ObservationReplayMessage = {
        role: "tool",
        tool_call_id: toolCallId(call, fallbackId),
        content: resultContent(pairedCall?.result),
      };
      if (name) msg.name = name;
      messages.push(msg);
    });
  }
  return messages;
}

export type RetestToolSource = "recorded" | "sibling" | "stub" | "none";

export type RetestToolDefinition = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: { type: "object"; properties: Record<string, unknown> };
  };
};

export type ResolveRetestToolsResult = {
  tools: unknown[];
  source: RetestToolSource;
};

export type ResolveRetestToolsArgs = {
  tools?: unknown;
  toolNames?: unknown;
  toolCalls?: unknown;
  siblingMetrics?: unknown;
};

function stringNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((n): n is string => typeof n === "string" && n.length > 0);
}

function nonEmptyTools(value: unknown): unknown[] | null {
  return Array.isArray(value) && value.length > 0 ? value : null;
}

function namesEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((name, i) => name === b[i]);
}

function metricPayload(metric: unknown): Record<string, unknown> | null {
  const rec = asRecord(metric);
  if (!rec) return null;
  return asRecord(rec.data) || rec;
}

function isModelCallMetric(metric: unknown): boolean {
  const rec = asRecord(metric);
  if (!rec) return false;
  const eventType = rec.event_type;
  return eventType === "model_call" || eventType == null || eventType === "";
}

function stubToolsFromNames(names: string[]): RetestToolDefinition[] {
  return names.map((name) => ({
    type: "function" as const,
    function: {
      name,
      description: "",
      parameters: { type: "object" as const, properties: {} },
    },
  }));
}

function namesFromToolCalls(toolCalls: unknown): string[] {
  const calls = collectMetricToolCalls([{ data: { tool_calls: toolCalls } }]);
  const seen = new Set<string>();
  const names: string[] = [];
  for (const call of calls) {
    if (seen.has(call.name)) continue;
    seen.add(call.name);
    names.push(call.name);
  }
  return names;
}

/**
 * Tools to send on Debug Interactions retest.
 *
 * Full schemas are opt-in on the model action, so most historical
 * model_call events only have names. Prefer recorded defs, then a
 * sibling tick of the same surface, then name-only stubs.
 */
export function resolveRetestTools(
  args: ResolveRetestToolsArgs,
): ResolveRetestToolsResult {
  const recorded = nonEmptyTools(args.tools);
  if (recorded) {
    return { tools: recorded, source: "recorded" };
  }

  const names = stringNames(args.toolNames);
  const metrics = Array.isArray(args.siblingMetrics) ? args.siblingMetrics : [];
  let firstNonEmpty: unknown[] | null = null;

  for (const metric of metrics) {
    if (!isModelCallMetric(metric)) continue;
    const data = metricPayload(metric);
    if (!data) continue;
    const siblingTools = nonEmptyTools(data.tools);
    if (!siblingTools) continue;
    if (names.length === 0) {
      return { tools: siblingTools, source: "sibling" };
    }
    const siblingNames = stringNames(data.tool_names);
    if (siblingNames.length > 0 && namesEqual(names, siblingNames)) {
      return { tools: siblingTools, source: "sibling" };
    }
    if (!firstNonEmpty) firstNonEmpty = siblingTools;
  }

  const stubNames =
    names.length > 0 ? names : namesFromToolCalls(args.toolCalls);
  if (stubNames.length > 0) {
    return { tools: stubToolsFromNames(stubNames), source: "stub" };
  }
  if (firstNonEmpty) {
    return { tools: firstNonEmpty, source: "sibling" };
  }
  return { tools: [], source: "none" };
}
