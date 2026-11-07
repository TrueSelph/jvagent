/**
 * Per-message model usage footer for the assistant-ui thread surface.
 *
 * Expandable one-line summary (model · tokens · time · tok/s) under a completed
 * assistant message; expanding reveals a per-model_call table and a timing/cost
 * detail row. Sourced from the authoritative final payload jvchat carries on the
 * message metadata as `custom.debugMessage.debugData.interaction` (`usage` +
 * `observability_metrics`) — the same data the Debug dialog shows.
 *
 * Local history retains a bounded numeric receipt after the debug payload is stripped.
 * Hidden when neither a live payload nor a persisted receipt is available.
 */
import { useState, type FC } from "react";
import { ChevronDownIcon } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";

import { cn } from "@/lib/utils";
import type { JvAssistantMeta } from "@/lib/threadMessages";

import { messageObservability, type Usage } from "@/lib/messageObservability";

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
  const meta = useAuiState((s) => s.message.metadata?.custom as unknown as JvAssistantMeta | undefined);
  const interaction = messageObservability({
    debugData: meta?.debugMessage?.debugData,
    observability: meta?.observability,
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
  const attempts = (interaction.observability_metrics ?? []).filter(
    (m) => m.event_type === "model_attempt",
  );
  const attemptCount = usage.model_attempt_count ?? attempts.length;

  const totalIn = usage.prompt_tokens ?? 0;
  const totalOut = usage.completion_tokens ?? 0;
  const totalTokens =
    usage.total_tokens ?? (totalIn || totalOut ? totalIn + totalOut : 0);
  const durationSec = usage.total_duration_seconds;
  const tps =
    durationSec && durationSec > 0 && totalOut > 0
      ? totalOut / durationSec
      : undefined;

  const primaryModel = shortModel(
    interaction.model ?? steps[0]?.data?.model ?? attempts[0]?.data?.model,
  );
  const multiModel =
    steps.length > 1 &&
    new Set(steps.map((s) => s.data?.model).filter(Boolean)).size > 1;

  const summaryParts: string[] = [];
  if (steps.length > 0 || attemptCount > 0) {
    summaryParts.push(primaryModel);
  }
  if (attemptCount > 0) {
    summaryParts.push(
      `${attemptCount} attempt${attemptCount === 1 ? "" : "s"}`,
    );
  }
  if (totalTokens > 0) summaryParts.push(`${formatTokens(totalTokens)} tokens`);
  const timeStr = formatTimeSec(durationSec);
  if (timeStr) summaryParts.push(timeStr);
  const attemptTimeStr = formatTimeSec(usage.model_attempt_duration_seconds);
  if (attemptTimeStr && (usage.model_call_count ?? steps.length) === 0) {
    summaryParts.push(`${attemptTimeStr} provider time`);
  }
  const unknownCostCount = usage.unknown_cost_call_count ?? 0;
  if (unknownCostCount > 0) {
    summaryParts.push(
      `${unknownCostCount} unknown-cost event${unknownCostCount === 1 ? "" : "s"}`,
    );
  }
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
          {interaction.metricsTruncated && <p>Showing the first 128 model events; totals include all calls.</p>}
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

          {attemptCount > 0 && (
            <div className="mt-1.5" role="status">
              {attemptCount} provider attempt
              {attemptCount === 1 ? "" : "s"}
              {usage.failed_model_attempt_count
                ? ` · ${usage.failed_model_attempt_count} failed`
                : ""}
              {usage.cancelled_model_attempt_count
                ? ` · ${usage.cancelled_model_attempt_count} cancelled`
                : ""}
              {unknownCostCount > 0 && (
                <span className="ml-2 font-medium text-amber-700 dark:text-amber-400">
                  {" · "}
                  Cost unknown for {unknownCostCount} provider attempt
                  {unknownCostCount === 1 ? "" : "s"}; check provider billing.
                </span>
              )}
            </div>
          )}

          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-muted-foreground/70">
            {timeStr && <span>Total: {timeStr}</span>}
            {tpsStr && <span>{tpsStr}</span>}
            {totalTokens > 0 && (
              <span>{totalTokens.toLocaleString()} tokens</span>
            )}
            {typeof (usage.total_cost_usd ?? usage.estimated_cost_usd) === "number" &&
              (usage.total_cost_usd ?? usage.estimated_cost_usd ?? 0) > 0 && (
                <span title="Provider-reported cost plus estimated fallback cost">
                  {(usage.estimated_cost_usd ?? 0) > 0 ? "Estimated " : ""}${(usage.total_cost_usd ?? usage.estimated_cost_usd ?? 0).toFixed(4)}
                </span>
              )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MessageStats;
