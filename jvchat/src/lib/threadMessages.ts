/**
 * Convert jvchat's flat `Message[]` stream (user / assistant-text / separate
 * `category:"thought"` items) into assistant-ui `ThreadMessageLike[]`, where an
 * assistant turn is ONE message whose content parts are the reasoning, tool
 * calls, and final text. Each assistant turn carries its final-chunk `debugData`
 * on `metadata.custom` so the per-message Debug dialog can read it.
 */
import type { ThreadMessageLike } from "@assistant-ui/react";

import type { Message } from "../types/message";

/** Custom metadata we stash on assistant messages for jvchat-specific UI. */
export interface JvAssistantMeta {
  /** Discriminator so footers can tell which custom shape they hold. */
  jvRole: "assistant";
  /** The jvchat Message that carries the final-chunk debugData (if any). */
  debugMessage: Message | null;
  /** The jvchat id of the final answer message (for keys / actions). */
  jvMessageId: string;
  interactionId?: string;
  /** Branch root id of the turn this assistant answer belongs to. */
  branchRootId?: string;
}

/** Custom metadata we stash on user messages for jvchat-specific UI. */
export interface JvUserMeta {
  jvRole: "user";
  /** The jvchat id of this user message (for native edit → editAndResend). */
  jvUserId: string;
  /** Branch root id (own id unless this is an alternate version of a turn). */
  branchRootId: string;
}

type ToolPart = {
  readonly type: "tool-call";
  readonly toolCallId: string;
  readonly toolName: string;
  readonly args?: Record<string, unknown>;
  readonly result?: unknown;
  readonly isError?: boolean;
};

function userToThread(m: Message): ThreadMessageLike {
  const meta: JvUserMeta = {
    jvRole: "user",
    jvUserId: m.id,
    branchRootId: m.branchRootId ?? m.id,
  };
  const attachments = (m.attachments ?? []).map((a, i) => ({
    id: `${m.id}-att-${i}`,
    type: (a.kind === "image" ? "image" : "document") as "image" | "document",
    name: a.name,
    contentType: a.kind === "image" ? "image/*" : "application/octet-stream",
    status: { type: "complete" as const },
    content: [
      a.persistedDataUrl || a.previewUrl
        ? ({
            type: "image" as const,
            image: (a.persistedDataUrl || a.previewUrl)!,
          } as const)
        : ({ type: "text" as const, text: a.name } as const),
    ],
  }));
  return {
    role: "user",
    id: m.id,
    createdAt: new Date(m.timestamp),
    content: [{ type: "text", text: m.content }],
    ...(attachments.length ? { attachments: attachments as never } : {}),
    metadata: { custom: meta as unknown as Record<string, unknown> },
  };
}

/**
 * Collapse a run of consecutive assistant-side jvchat messages (reasoning/tool
 * thoughts + the final answer text) into one assistant ThreadMessageLike.
 */
function assistantGroupToThread(
  group: Message[],
  branchRootId?: string,
): ThreadMessageLike {
  const parts: Array<
    | { type: "text"; text: string }
    | { type: "reasoning"; text: string }
    | ToolPart
  > = [];

  // Pair tool_call + tool_result by segmentId so they fold into one part.
  const toolBySegment = new Map<string, ToolPart>();
  const meta: JvAssistantMeta = {
    jvRole: "assistant",
    debugMessage: null,
    jvMessageId: group[group.length - 1]?.id ?? "",
    ...(branchRootId ? { branchRootId } : {}),
  };
  let streaming = false;
  let createdAt: string | undefined;

  for (const m of group) {
    if (m.streaming) streaming = true;
    if (m.debugData) meta.debugMessage = m;
    if (m.interactionId) meta.interactionId = m.interactionId;

    if (m.category === "thought") {
      const md = (m.metadata ?? {}) as Record<string, unknown>;
      if (m.thoughtType === "reasoning") {
        if (m.content.trim()) parts.push({ type: "reasoning", text: m.content });
      } else if (m.thoughtType === "tool_call") {
        const seg = m.segmentId || m.id;
        const part: ToolPart = {
          type: "tool-call",
          toolCallId: seg,
          toolName: String(md.tool_name ?? m.content ?? "tool"),
          args: (md.tool_args as Record<string, unknown>) ?? {},
        };
        toolBySegment.set(seg, part);
        parts.push(part);
      } else if (m.thoughtType === "tool_result") {
        const seg = m.segmentId || m.id;
        const existing = toolBySegment.get(seg);
        const result = md.tool_result ?? m.content;
        const isError = Boolean(md.is_error);
        if (existing) {
          // Mutate the already-pushed part in place (same array reference).
          const idx = parts.indexOf(existing);
          if (idx >= 0)
            parts[idx] = { ...existing, result, isError } as ToolPart;
        } else {
          parts.push({
            type: "tool-call",
            toolCallId: seg,
            toolName: String(md.tool_name ?? "tool"),
            result,
            isError,
          });
        }
      }
      // thoughtType === "status" (acks) → ephemeral; omit from the transcript.
    } else {
      // Final answer text (category "user"/undefined on an assistant message).
      meta.jvMessageId = m.id;
      if (!meta.debugMessage && m.debugData) meta.debugMessage = m;
      if (m.content) parts.push({ type: "text", text: m.content });
      createdAt = m.timestamp;
    }
  }

  if (parts.length === 0) parts.push({ type: "text", text: "" });

  // Stable turn id: anchor to the interaction (or the first item), NOT the last
  // item — the last item changes as reasoning/tool/text parts stream in, which
  // would make assistant-ui treat each streaming update as a new branch (the
  // "version numbers increment" bug).
  const stableId = meta.interactionId
    ? `turn-${meta.interactionId}`
    : (group[0]?.id ?? meta.jvMessageId);

  return {
    role: "assistant",
    id: stableId,
    ...(createdAt ? { createdAt: new Date(createdAt) } : {}),
    status: { type: streaming ? "running" : "complete" } as never,
    content: parts as never,
    metadata: { custom: meta as unknown as Record<string, unknown> },
  };
}

/** Group jvchat's flat message stream into assistant-ui thread messages. */
export function buildThreadMessages(messages: Message[]): ThreadMessageLike[] {
  const out: ThreadMessageLike[] = [];
  let i = 0;
  let lastRootId: string | undefined;
  while (i < messages.length) {
    const m = messages[i];
    if (m.role === "user") {
      lastRootId = m.branchRootId ?? m.id;
      out.push(userToThread(m));
      i++;
      continue;
    }
    const group: Message[] = [];
    while (i < messages.length && messages[i].role === "assistant") {
      group.push(messages[i]);
      i++;
    }
    if (group.length) out.push(assistantGroupToThread(group, lastRootId));
  }
  return out;
}
