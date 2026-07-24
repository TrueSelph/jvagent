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
