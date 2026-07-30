/**
 * Wire types for the jvagent interact SSE stream. Mirrors the server contract
 * (jvagent/action/response/message.py to_dict + the SSE envelope in
 * jvagent/action/interact/endpoints.py). Kept minimal — only the fields the
 * messenger consumes.
 */

/** message_type on a streamed ResponseMessage. */
export type MessageType = "stream_chunk" | "final" | "adhoc";

/** category distinguishes user-facing text from internal reasoning/tool rows. */
export type MessageCategory = "user" | "thought";

/** thought_type present only on category:"thought" rows. */
export type ThoughtType = "reasoning" | "tool_call" | "tool_result" | "status";

/** Agent-driven follow-up affordances, carried on a message's `metadata`.
 * `suggestions` are quick replies (the label is sent as the utterance);
 * `actions` send an explicit `value` distinct from the visible `label`. */
export interface MessageAction {
  label: string;
  value: string;
}

/** Normalize a raw metadata blob into a flat {label,value}[] chip list. */
export function extractSuggestions(
  metadata: Record<string, unknown> | undefined | null
): MessageAction[] {
  if (!metadata) return [];
  const out: MessageAction[] = [];
  const sugg = metadata.suggestions;
  if (Array.isArray(sugg)) {
    for (const s of sugg) {
      if (typeof s === "string" && s.trim()) out.push({ label: s, value: s });
    }
  }
  const actions = metadata.actions;
  if (Array.isArray(actions)) {
    for (const a of actions) {
      if (a && typeof a === "object") {
        const label = String((a as any).label ?? "").trim();
        const value = String((a as any).value ?? (a as any).label ?? "").trim();
        if (label && value) out.push({ label, value });
      }
    }
  }
  return out;
}

// ── Agent-driven UI components (static generative UI) ──────────────────────

/** Envelope version this client understands. */
export const UI_ENVELOPE_VERSION = 1;

/** Components the frontend owns. The agent may only pick from this catalog. */
export const UI_COMPONENTS = ["card", "choices"] as const;
export type UiComponent = (typeof UI_COMPONENTS)[number];

/**
 * One agent-rendered component, carried on `metadata.ui`.
 *
 * A single namespaced key (rather than sibling top-level keys) because
 * `visitor.data` is merged over action metadata server-side — one reserved name
 * is one name to defend.
 *
 * `fallback` is load-bearing, not decorative: it is what renders on version
 * skew, an unknown component, or a malformed payload, and what appears in the
 * downloaded transcript. Anything without it degrades to nothing.
 */
export interface UiEnvelope {
  v: number;
  component: string;
  id: string;
  props: Record<string, unknown>;
  fallback?: string;
}

function coerceEnvelope(raw: unknown): UiEnvelope | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const component = typeof o.component === "string" ? o.component : "";
  const id = typeof o.id === "string" ? o.id : "";
  if (!component || !id) return null;
  return {
    v: typeof o.v === "number" ? o.v : 1,
    component,
    id,
    props: (o.props && typeof o.props === "object"
      ? o.props
      : {}) as Record<string, unknown>,
    fallback: typeof o.fallback === "string" ? o.fallback : undefined,
  };
}

/**
 * Normalize `metadata.ui` into a list of envelopes. Accepts a single envelope
 * or an array. Malformed entries are dropped rather than thrown — a bad payload
 * must never break a turn.
 */
export function extractUiComponents(
  metadata: Record<string, unknown> | undefined | null
): UiEnvelope[] {
  if (!metadata) return [];
  const raw = (metadata as Record<string, unknown>).ui;
  if (!raw) return [];
  const list = Array.isArray(raw) ? raw : [raw];
  const out: UiEnvelope[] = [];
  const seen = new Set<string>();
  for (const item of list) {
    const env = coerceEnvelope(item);
    if (!env || seen.has(env.id)) continue;
    seen.add(env.id);
    out.push(env);
  }
  return out;
}

/** True when this client can render the component natively. */
export function isRenderableComponent(env: UiEnvelope): boolean {
  return (
    env.v <= UI_ENVELOPE_VERSION &&
    (UI_COMPONENTS as readonly string[]).includes(env.component)
  );
}

/** A single streamed ResponseMessage (chunk.message). */
export interface ResponseMessageData {
  id?: string;
  session_id?: string;
  user_id?: string;
  interaction_id?: string;
  message_type: MessageType;
  content: string;
  channel?: string;
  category: MessageCategory;
  thought_type?: ThoughtType | null;
  segment_id?: string;
  metadata?: Record<string, unknown>;
  timestamp?: string;
}

/** SSE frame envelope. */
export interface SSEChunk {
  type: "start" | "message" | "final" | "error";
  interaction_id?: string;
  session_id?: string;
  user_id?: string;
  session_token?: string;
  message?: ResponseMessageData | string;
  request_id?: string;
}
