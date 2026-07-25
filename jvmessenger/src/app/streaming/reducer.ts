/**
 * Pure reducer for a single assistant turn.
 *
 * The interact SSE stream delivers a turn as many `ResponseMessage` rows. This
 * module folds them into a `TurnState` with **no React and no I/O**, so the
 * fiddly parts (segmentation, thought routing, dedup) are unit-testable on their
 * own. `useChatRuntime` owns the React state; this owns the semantics.
 *
 * What it fixes versus the previous inline accumulator:
 * - **`segment_id` is honoured.** The server mints it to separate distinct
 *   replies within one turn; concatenating them ran an adhoc notice straight
 *   into the model's answer with no break.
 * - **`thought_type` is routed, not flattened.** `reasoning` accumulates as
 *   text, while `tool_call` / `tool_result` / `status` become a live activity
 *   list ("Searching products…") the UI can show even when reasoning is masked.
 */

import {
  extractSuggestions,
  extractUiComponents,
  type MessageAction,
  type ResponseMessageData,
  type UiEnvelope,
} from "./types";

/** One visible chunk of the answer, keyed by the server's `segment_id`. */
export interface AnswerSegment {
  id: string;
  text: string;
}

/** A tool/status row surfaced as live progress. */
export interface ActivityEntry {
  id: string;
  label: string;
  status: "running" | "done" | "error";
}

export interface TurnState {
  segments: AnswerSegment[];
  /** Accumulated `thought_type:"reasoning"` text (masked unless showReasoning). */
  reasoning: string;
  activity: ActivityEntry[];
  suggestions: MessageAction[];
  /** Agent-rendered components for this turn, in arrival order. */
  ui: UiEnvelope[];
  /** Set when the turn failed; rendered as a distinct part, never as answer text. */
  error?: string;
}

/** Segment key used when the server sends no `segment_id`. */
const DEFAULT_SEGMENT = "_";

export function emptyTurn(): TurnState {
  return { segments: [], reasoning: "", activity: [], suggestions: [], ui: [] };
}

/** "storefront__search_products" → "search products" (for the activity label). */
export function humanizeToolName(name: string): string {
  const tail = (name || "").split("__").pop() || name || "";
  return tail.replace(/[_-]+/g, " ").trim();
}

/** Concatenated visible answer across all segments (blank segments dropped). */
export function answerText(state: TurnState): string {
  return state.segments
    .map((s) => s.text)
    .filter((t) => t.trim())
    .join("\n\n");
}

/** True once any visible answer text has arrived. */
export function hasAnswer(state: TurnState): boolean {
  return state.segments.some((s) => s.text.trim().length > 0);
}

function appendToSegment(
  segments: AnswerSegment[],
  id: string,
  text: string
): AnswerSegment[] {
  const idx = segments.findIndex((s) => s.id === id);
  if (idx === -1) return [...segments, { id, text }];
  const next = segments.slice();
  next[idx] = { ...next[idx], text: next[idx].text + text };
  return next;
}

function upsertActivity(
  activity: ActivityEntry[],
  entry: ActivityEntry
): ActivityEntry[] {
  const idx = activity.findIndex((a) => a.id === entry.id);
  if (idx === -1) return [...activity, entry];
  const next = activity.slice();
  next[idx] = { ...next[idx], ...entry };
  return next;
}

function reduceThought(state: TurnState, msg: ResponseMessageData): TurnState {
  const meta = (msg.metadata ?? {}) as Record<string, unknown>;
  const content = msg.content ?? "";

  switch (msg.thought_type) {
    case "reasoning":
      if (!content) return state;
      return {
        ...state,
        reasoning: state.reasoning ? `${state.reasoning}\n${content}` : content,
      };

    case "tool_call": {
      const toolName = String(meta.tool_name ?? "").trim();
      const id = msg.segment_id || toolName || `act${state.activity.length}`;
      return {
        ...state,
        activity: upsertActivity(state.activity, {
          id,
          label: humanizeToolName(toolName) || "working",
          status: "running",
        }),
      };
    }

    case "tool_result": {
      const toolName = String(meta.tool_name ?? "").trim();
      const id = msg.segment_id || toolName;
      if (!id) return state;
      const existing = state.activity.find((a) => a.id === id);
      return {
        ...state,
        activity: upsertActivity(state.activity, {
          id,
          label: existing?.label ?? (humanizeToolName(toolName) || "working"),
          status: meta.is_error ? "error" : "done",
        }),
      };
    }

    case "status":
      if (!content.trim()) return state;
      return {
        ...state,
        activity: upsertActivity(state.activity, {
          id: msg.segment_id || "status",
          label: content.trim(),
          status: "running",
        }),
      };

    default:
      return state;
  }
}

/**
 * Fold one streamed message into the turn.
 *
 * Returns the same object reference when nothing changed, so callers can skip
 * a re-render cheaply.
 */
export function reduceMessage(
  state: TurnState,
  msg: ResponseMessageData
): TurnState {
  let next = state;

  // Suggestions ride on any message; the last non-empty set for the turn wins.
  const suggestions = extractSuggestions(msg.metadata);
  if (suggestions.length) next = { ...next, suggestions };

  // UI components accumulate (deduped by id) — unlike suggestions they are not
  // replaced, since each is a distinct thing the agent chose to show.
  const ui = extractUiComponents(msg.metadata);
  if (ui.length) {
    const known = new Set(next.ui.map((e) => e.id));
    const fresh = ui.filter((e) => !known.has(e.id));
    if (fresh.length) next = { ...next, ui: [...next.ui, ...fresh] };
  }

  if (msg.category === "thought") return reduceThought(next, msg);

  const content = msg.content ?? "";
  const segmentId = msg.segment_id || DEFAULT_SEGMENT;

  if (msg.message_type === "stream_chunk" || msg.message_type === "adhoc") {
    if (!content) return next;
    return { ...next, segments: appendToSegment(next.segments, segmentId, content) };
  }

  if (msg.message_type === "final") {
    // `final` is a consolidated echo — only use it when nothing streamed, so a
    // non-streaming turn still renders.
    if (!content || hasAnswer(next)) return next;
    return { ...next, segments: appendToSegment(next.segments, segmentId, content) };
  }

  return next;
}

/** Mark the turn as failed. The message is kept out of the answer text. */
export function withError(state: TurnState, error: string): TurnState {
  return {
    ...state,
    error,
    // Any still-running activity is moot once the turn failed.
    activity: state.activity.map((a) =>
      a.status === "running" ? { ...a, status: "error" as const } : a
    ),
  };
}
