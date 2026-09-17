/** Shared request+result snapshot for Debug Interactions copy / retest / export. */

import type { RetestToolSource } from "./debugToolCalls";

export type ReplayToolSource = RetestToolSource;

export type ReplaySnapshot = {
  model: string;
  provider: string;
  system: string;
  user: string;
  history: unknown[];
  replay: unknown[];
  tools: unknown[];
  toolChoice?: unknown;
  parallelToolCalls?: boolean;
  temperature?: number;
  maxTokens?: number;
  response: string;
  toolCalls: unknown[];
  finishReason: string;
  calledBy?: string;
  usage?: unknown;
  toolSource: ReplayToolSource;
};

export type ParseJsonArrayResult =
  | { ok: true; value: unknown[] }
  | { ok: false; error: string };

export function parseJsonArray(
  text: string,
  label: string,
): ParseJsonArrayResult {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: [] };
  try {
    const parsed = JSON.parse(trimmed);
    if (!Array.isArray(parsed)) {
      return { ok: false, error: `Cannot retest: ${label} must be an array.` };
    }
    return { ok: true, value: parsed };
  } catch {
    return { ok: false, error: `Cannot retest: ${label} is not valid JSON.` };
  }
}

export type BuildReplaySnapshotArgs = {
  user: string;
  system: string;
  historyText: string;
  replayText: string;
  toolsText: string;
  model: string;
  provider: string;
  response?: string;
  toolCalls?: unknown;
  finishReason?: string;
  calledBy?: string;
  usage?: unknown;
  toolSource?: ReplayToolSource;
  temperature?: number;
  maxTokens?: number;
  toolChoice?: unknown;
  parallelToolCalls?: boolean;
};

export type BuildReplaySnapshotResult =
  | { ok: true; snapshot: ReplaySnapshot }
  | { ok: false; error: string };

function asUnknownArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function buildReplaySnapshot(
  args: BuildReplaySnapshotArgs,
): BuildReplaySnapshotResult {
  const parsedHistory = parseJsonArray(args.historyText, "History (JSON)");
  if (!parsedHistory.ok) return parsedHistory;
  const parsedReplay = parseJsonArray(
    args.replayText,
    "This-turn tool replay (JSON)",
  );
  if (!parsedReplay.ok) return parsedReplay;
  const parsedTools = parseJsonArray(args.toolsText, "Tools (JSON)");
  if (!parsedTools.ok) return parsedTools;

  return {
    ok: true,
    snapshot: {
      model: args.model,
      provider: args.provider,
      system: args.system,
      user: args.user,
      history: parsedHistory.value,
      replay: parsedReplay.value,
      tools: parsedTools.value,
      toolChoice: args.toolChoice,
      parallelToolCalls: args.parallelToolCalls,
      temperature: args.temperature,
      maxTokens: args.maxTokens,
      response: args.response || "",
      toolCalls: asUnknownArray(args.toolCalls),
      finishReason: args.finishReason || "",
      calledBy: args.calledBy,
      usage: args.usage,
      toolSource: args.toolSource || "none",
    },
  };
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/**
 * LiteLLM matches the provider prefix before the first `/` case-sensitively
 * (`openai`, not `Openai` / `OpenAI`). Lowercase only that first segment.
 */
export function normalizeLiteLLMModelId(model: string): string {
  const ident = model.trim();
  const slash = ident.indexOf("/");
  if (slash <= 0) return ident;
  return ident.slice(0, slash).toLowerCase() + ident.slice(slash);
}

/** Orchestrator-shaped /query body: messages + tools when present. */
export function buildQueryPayload(
  snapshot: ReplaySnapshot,
): Record<string, unknown> {
  const messages: Record<string, unknown>[] = [];
  if (snapshot.system) {
    messages.push({ role: "system", content: snapshot.system });
  }
  if (snapshot.history.length > 0) {
    messages.push(...(snapshot.history as Record<string, unknown>[]));
  }
  messages.push({ role: "user", content: snapshot.user });
  if (snapshot.replay.length > 0) {
    messages.push(...(snapshot.replay as Record<string, unknown>[]));
  }

  const payload: Record<string, unknown> = {
    model: normalizeLiteLLMModelId(snapshot.model),
    messages,
  };
  if (snapshot.provider) {
    payload.provider = snapshot.provider;
  }
  if (snapshot.tools.length > 0) {
    payload.tools = snapshot.tools;
    payload.tool_choice =
      snapshot.toolChoice !== undefined ? snapshot.toolChoice : "auto";
    payload.parallel_tool_calls =
      snapshot.parallelToolCalls !== undefined
        ? snapshot.parallelToolCalls
        : false;
  }
  if (typeof snapshot.temperature === "number") {
    payload.temperature = snapshot.temperature;
  }
  if (typeof snapshot.maxTokens === "number") {
    payload.max_tokens = snapshot.maxTokens;
  }
  return payload;
}

/**
 * Full request dump plus expected result. Improve instruction is optional
 * (Improve Prompt copy / Improve query only).
 */
export function formatCopyPrompt(
  snapshot: ReplaySnapshot,
  improveInstruction?: string,
): string {
  const lines = [
    `Model: ${snapshot.model || "(none)"}`,
    `Provider: ${snapshot.provider || "(none)"}`,
  ];
  if (snapshot.calledBy) {
    lines.push(`Called by: ${snapshot.calledBy}`);
  }
  if (typeof snapshot.temperature === "number") {
    lines.push(`Temperature: ${snapshot.temperature}`);
  }
  if (typeof snapshot.maxTokens === "number") {
    lines.push(`Max tokens: ${snapshot.maxTokens}`);
  }
  lines.push(
    "",
    "System Prompt:",
    snapshot.system || "(empty)",
    "",
    "User Prompt:",
    snapshot.user || "(empty)",
    "",
    "Conversation History:",
    pretty(snapshot.history),
    "",
    "This-turn tool replay:",
    pretty(snapshot.replay),
    "",
    "Tools:",
    pretty(snapshot.tools),
    "",
    "Expected result:",
    "Response:",
    snapshot.response || "(empty)",
    "",
    "tool_calls:",
    pretty(snapshot.toolCalls),
    "",
    `finish_reason: ${snapshot.finishReason || "(none)"}`,
  );
  if (improveInstruction !== undefined) {
    lines.push(
      "",
      "Improvement Instruction:",
      improveInstruction || "(none)",
      "",
      "Provide improvement instruction on how to improve the prompt. Return a raw markdown.",
    );
  }
  return lines.join("\n");
}

export function formatImproveSystemPrompt(): string {
  return "You are a prompt engineering expert. Analyze the given prompts and improve them based on the instruction.";
}

export const EXPORT_VERSION = 2;

export type DebugExportSelection = {
  user_prompt: string;
  system_prompt: string;
  historyText: string;
  replayText: string;
  toolsText: string;
  replayModel: string;
  provider: string;
  testResult: unknown;
};

export type DebugExportV2 = {
  version: typeof EXPORT_VERSION;
  parentInteractions: unknown[];
  pagination: unknown;
  selectedParentIndex: number | null;
  selectedMetricIndex: number | null;
  selection: DebugExportSelection | null;
  metadata: { exportedAt: string; agentId: string | null };
};

export function buildExportV2(args: {
  parentInteractions: unknown[];
  pagination: unknown;
  selectedParentIndex: number | null;
  selectedMetricIndex: number | null;
  selection: DebugExportSelection | null;
  agentId: string | null;
  exportedAt?: string;
}): DebugExportV2 {
  return {
    version: EXPORT_VERSION,
    parentInteractions: args.parentInteractions,
    pagination: args.pagination,
    selectedParentIndex: args.selectedParentIndex,
    selectedMetricIndex: args.selectedMetricIndex,
    selection: args.selection,
    metadata: {
      exportedAt: args.exportedAt || new Date().toISOString(),
      agentId: args.agentId,
    },
  };
}

export type ParsedImport =
  | {
      kind: "v2";
      parentInteractions: unknown[];
      pagination: unknown;
      selectedParentIndex: number;
      selectedMetricIndex: number;
      selection: DebugExportSelection | null;
    }
  | {
      kind: "v1";
      parentInteractions: unknown[];
      pagination: unknown;
      selectedParentIndex: number;
      selectedMetricIndex: number;
    }
  | {
      kind: "legacy";
      interaction: Record<string, unknown>;
      testResult: unknown;
    }
  | { kind: "invalid"; error: string };

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function selectionFromUnknown(value: unknown): DebugExportSelection | null {
  const rec = asRecord(value);
  if (!rec) return null;
  return {
    user_prompt: typeof rec.user_prompt === "string" ? rec.user_prompt : "",
    system_prompt:
      typeof rec.system_prompt === "string" ? rec.system_prompt : "",
    historyText: typeof rec.historyText === "string" ? rec.historyText : "",
    replayText: typeof rec.replayText === "string" ? rec.replayText : "[]",
    toolsText: typeof rec.toolsText === "string" ? rec.toolsText : "[]",
    replayModel: typeof rec.replayModel === "string" ? rec.replayModel : "",
    provider: typeof rec.provider === "string" ? rec.provider : "",
    testResult: rec.testResult ?? null,
  };
}

export function parseImportFile(parsed: unknown): ParsedImport {
  const rec = asRecord(parsed);
  if (!rec) {
    return { kind: "invalid", error: "Invalid import file format" };
  }

  if (Array.isArray(rec.parentInteractions)) {
    const pIdx =
      typeof rec.selectedParentIndex === "number" ? rec.selectedParentIndex : 0;
    const mIdx =
      typeof rec.selectedMetricIndex === "number" ? rec.selectedMetricIndex : 0;
    if (rec.version === EXPORT_VERSION || rec.selection) {
      return {
        kind: "v2",
        parentInteractions: rec.parentInteractions,
        pagination: rec.pagination ?? null,
        selectedParentIndex: pIdx,
        selectedMetricIndex: mIdx,
        selection: selectionFromUnknown(rec.selection),
      };
    }
    return {
      kind: "v1",
      parentInteractions: rec.parentInteractions,
      pagination: rec.pagination ?? null,
      selectedParentIndex: pIdx,
      selectedMetricIndex: mIdx,
    };
  }

  const interactionData = asRecord(rec.interaction) || rec;
  if (interactionData?.data) {
    return {
      kind: "legacy",
      interaction: interactionData,
      testResult: rec.testResult ?? null,
    };
  }
  return { kind: "invalid", error: "Invalid import file format" };
}

/** Unwrap jvspatial `{ success, data }` envelopes from POST /actions/{id}/query. */
export function unwrapQueryActionResponse(data: unknown): Record<string, unknown> {
  const rec = asRecord(data);
  if (!rec) return {};
  if (rec.success && rec.data && typeof rec.data === "object" && rec.data !== null) {
    return rec.data as Record<string, unknown>;
  }
  return rec;
}
