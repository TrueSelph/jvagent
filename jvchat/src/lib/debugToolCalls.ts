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
