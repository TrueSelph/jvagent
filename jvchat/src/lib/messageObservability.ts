/** Small numeric receipt persisted independently of private SSE debug payloads. */
export type Usage = Partial<Record<
  | "prompt_tokens" | "completion_tokens" | "total_tokens" | "total_duration_seconds"
  | "estimated_cost_usd" | "reported_cost_usd" | "litellm_cost_usd" | "total_cost_usd"
  | "model_call_count" | "model_attempt_count" | "failed_model_attempt_count" | "cancelled_model_attempt_count"
  | "model_attempt_duration_seconds" | "unknown_cost_call_count", number>>;

export type MetricEntry = {
  event_type: "model_call" | "model_attempt";
  data: { model?: string; duration?: number; outcome?: string;
    usage?: { prompt_tokens?: number; completion_tokens?: number } };
};

export type MessageObservability = {
  usage: Usage;
  observability_metrics: MetricEntry[];
  metricsTruncated?: boolean;
};

const usageKeys: (keyof Usage)[] = [
  "prompt_tokens", "completion_tokens", "total_tokens", "total_duration_seconds",
  "estimated_cost_usd", "reported_cost_usd", "litellm_cost_usd", "total_cost_usd",
  "model_call_count", "model_attempt_count", "failed_model_attempt_count", "cancelled_model_attempt_count",
  "model_attempt_duration_seconds", "unknown_cost_call_count",
];
const record = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const number = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : undefined;

export function messageObservability(message: {
  debugData?: unknown; observability?: unknown;
}): MessageObservability | undefined {
  const live = record(record(message.debugData).interaction);
  const source = live.usage || live.observability_metrics ? live : record(message.observability);
  const usage: Usage = {};
  const rawUsage = record(source.usage);
  for (const key of usageKeys) {
    const value = number(rawUsage[key]);
    if (value !== undefined) usage[key] = value;
  }
  const metrics: MetricEntry[] = [];
  let matching = 0;
  if (Array.isArray(source.observability_metrics)) {
    for (const raw of source.observability_metrics) {
      const entry = record(raw);
      if (entry.event_type !== "model_call" && entry.event_type !== "model_attempt") continue;
      matching++;
      if (metrics.length >= 128) continue;
      const data = record(entry.data);
      const tokens = record(data.usage);
      metrics.push({
        event_type: entry.event_type,
        data: {
          ...(typeof data.model === "string" ? { model: data.model.slice(0, 128) } : {}),
          duration: number(data.duration),
          ...(["succeeded", "failed", "cancelled", "success"].includes(String(data.outcome))
            ? { outcome: String(data.outcome) } : {}),
          usage: { prompt_tokens: number(tokens.prompt_tokens), completion_tokens: number(tokens.completion_tokens) },
        },
      });
    }
  }
  if (!Object.keys(usage).length && !metrics.length) return undefined;
  return { usage, observability_metrics: metrics,
    ...(matching > 128 || source.metricsTruncated === true ? { metricsTruncated: true } : {}) };
}
