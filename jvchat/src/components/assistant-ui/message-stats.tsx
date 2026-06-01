/**
 * Per-message stats footer — jvchat port of integral's MessageObservability,
 * for the assistant-ui thread surface.
 *
 * Expandable one-line summary (model · tokens · time · tok/s) under a completed
 * assistant message; expanding reveals a per-model_call table and a timing/cost
 * detail row. Sourced from the authoritative final payload jvchat carries on the
 * message metadata as `custom.debugMessage.debugData.interaction` (`usage` +
 * `observability_metrics`) — the same data the Debug dialog shows.
 *
 * Hidden when no debug payload is present (e.g. server in production mode, which
 * strips usage/observability_metrics from the final chunk).
 */
import { useState, type FC } from "react";
import { ChevronDownIcon } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";

import { cn } from "@/lib/utils";
import type { JvAssistantMeta } from "@/lib/threadMessages";

type Usage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_duration_seconds?: number;
  estimated_cost_usd?: number;
};

type MetricEntry = {
  event_type?: string;
  data?: {
    model?: string;
    duration?: number;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
    };
  };
};

function formatTokens(n: number): string {
  return n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : String(n);
}

function formatTimeSec(seconds: number | undefined): string | null {
  if (seconds == null || seconds <= 0) return null;
  const ms = seconds * 1000;
  return ms >= 1_000 ? `${(ms / 1_000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function shortModel(modelId: string | undefined): string {
  if (!modelId) return "unknown";
  const parts = modelId.split("/");
  return parts[parts.length - 1];
}

export const MessageStats: FC = () => {
  const [expanded, setExpanded] = useState(false);

  // jvchat carries the final-chunk payload on the assistant message metadata.
  const interaction = useAuiState((s) => {
    const meta = s.message.metadata?.custom as unknown as
      | JvAssistantMeta
      | undefined;
    return (
      (meta?.debugMessage?.debugData as
        | { interaction?: { usage?: Usage; observability_metrics?: MetricEntry[] } }
        | undefined)?.interaction ?? undefined
    );
  });
  const running = useAuiState(
    (s) =>
      s.message.role === "assistant" && s.message.status?.type === "running",
  );

  if (running || !interaction) return null;

  const usage: Usage = interaction.usage ?? {};
  const steps = (interaction.observability_metrics ?? []).filter(
    (m) => m.event_type === "model_call",
  );

  const totalIn = usage.prompt_tokens ?? 0;
  const totalOut = usage.completion_tokens ?? 0;
  const totalTokens =
    usage.total_tokens ?? (totalIn || totalOut ? totalIn + totalOut : 0);
  const durationSec = usage.total_duration_seconds;
  const tps =
    durationSec && durationSec > 0 && totalOut > 0
      ? totalOut / durationSec
      : undefined;

  const primaryModel = shortModel(steps[0]?.data?.model);
  const multiModel =
    steps.length > 1 &&
    new Set(steps.map((s) => s.data?.model).filter(Boolean)).size > 1;

  const summaryParts: string[] = [];
  if (steps.length > 0) summaryParts.push(primaryModel);
  if (totalTokens > 0) summaryParts.push(`${formatTokens(totalTokens)} tokens`);
  const timeStr = formatTimeSec(durationSec);
  if (timeStr) summaryParts.push(timeStr);
  const tpsStr = tps ? `${tps.toFixed(1)} tok/s` : null;
  if (tpsStr) summaryParts.push(tpsStr);

  if (summaryParts.length === 0) return null;

  return (
    <div className="mt-2 w-full">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70 transition-colors hover:text-muted-foreground"
      >
        <ChevronDownIcon
          size={10}
          className={cn("transition-transform", expanded ? "" : "-rotate-90")}
        />
        <span className="tabular-nums">
          {summaryParts.join(" · ")}
          {multiModel ? ` (+${steps.length - 1} steps)` : ""}
        </span>
      </button>

      {expanded && (
        <div className="mt-1.5 rounded-md bg-muted px-3 py-2 text-[11px] text-muted-foreground">
          {steps.length > 0 && (
            <table className="w-full text-left tabular-nums">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
                  <th className="pb-1 pr-3 font-medium">Step</th>
                  <th className="pb-1 pr-3 font-medium">Model</th>
                  <th className="pb-1 pr-3 text-right font-medium">In</th>
                  <th className="pb-1 pr-3 text-right font-medium">Out</th>
                  <th className="pb-1 text-right font-medium">Time</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step, i) => (
                  <tr key={i}>
                    <td className="py-0.5 pr-3">{i + 1}</td>
                    <td className="py-0.5 pr-3 font-mono text-[10px]">
                      {shortModel(step.data?.model)}
                    </td>
                    <td className="py-0.5 pr-3 text-right">
                      {formatTokens(step.data?.usage?.prompt_tokens ?? 0)}
                    </td>
                    <td className="py-0.5 pr-3 text-right">
                      {formatTokens(step.data?.usage?.completion_tokens ?? 0)}
                    </td>
                    <td className="py-0.5 text-right">
                      {formatTimeSec(step.data?.duration) ?? "—"}
                    </td>
                  </tr>
                ))}
                {steps.length > 1 && (
                  <tr className="border-t border-border text-foreground">
                    <td className="pr-3 pt-1" colSpan={2}>
                      Total
                    </td>
                    <td className="pr-3 pt-1 text-right">
                      {formatTokens(totalIn)}
                    </td>
                    <td className="pr-3 pt-1 text-right">
                      {formatTokens(totalOut)}
                    </td>
                    <td className="pt-1" />
                  </tr>
                )}
              </tbody>
            </table>
          )}

          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-muted-foreground/70">
            {timeStr && <span>Total: {timeStr}</span>}
            {tpsStr && <span>{tpsStr}</span>}
            {totalTokens > 0 && (
              <span>{totalTokens.toLocaleString()} tokens</span>
            )}
            {typeof usage.estimated_cost_usd === "number" &&
              usage.estimated_cost_usd > 0 && (
                <span>${usage.estimated_cost_usd.toFixed(4)}</span>
              )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MessageStats;
