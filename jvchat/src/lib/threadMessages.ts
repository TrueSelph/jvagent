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
  /** Live "thinking status" line shown on the Reasoning trigger while running. */
  statusLabel?: string;
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
function firstLine(s: string): string {
  const line = s.split("\n").find((l) => l.trim()) ?? s;
  const t = line.trim();
  return t.length > 64 ? t.slice(0, 63) + "…" : t;
}

function assistantGroupToThread(
  group: Message[],
  branchRootId?: string,
  isRunning?: boolean,
): ThreadMessageLike {
  // Collect by kind so the whole exchange renders as ONE reasoning section and
  // ONE tool-call section (assistant-ui groups consecutive same-type parts).
  // Interleaving in arrival order would otherwise produce a separate collapsible
  // per reasoning/tool tick.
  const reasoningParts: Array<{ type: "reasoning"; text: string }> = [];
  const toolParts: ToolPart[] = [];
  const textParts: Array<{ type: "text"; text: string }> = [];

  // Pair tool_call + tool_result by segmentId so they fold into one part.
  const toolBySegment = new Map<string, number>();
  const meta: JvAssistantMeta = {
    jvRole: "assistant",
    debugMessage: null,
    jvMessageId: group[group.length - 1]?.id ?? "",
    ...(branchRootId ? { branchRootId } : {}),
  };
  let streaming = false;
  let createdAt: string | undefined;
  let statusLabel: string | undefined;

  for (const m of group) {
    if (m.streaming) streaming = true;
    if (m.debugData) meta.debugMessage = m;
    if (m.interactionId) meta.interactionId = m.interactionId;

    if (m.category === "thought") {
      const md = (m.metadata ?? {}) as Record<string, unknown>;
      if (m.thoughtType === "reasoning") {
        if (m.content.trim()) {
          reasoningParts.push({ type: "reasoning", text: m.content });
          statusLabel = firstLine(m.content);
        }
      } else if (m.thoughtType === "tool_call") {
        const seg = m.segmentId || m.id;
        const toolName = String(md.tool_name ?? m.content ?? "tool");
        toolBySegment.set(seg, toolParts.length);
        toolParts.push({
          type: "tool-call",
          toolCallId: seg,
          toolName,
          args: (md.tool_args as Record<string, unknown>) ?? {},
        });
        statusLabel = `Using ${toolName}…`;
      } else if (m.thoughtType === "tool_result") {
        const seg = m.segmentId || m.id;
        const result = md.tool_result ?? m.content;
        const isError = Boolean(md.is_error);
        const idx = toolBySegment.get(seg);
        if (idx !== undefined && toolParts[idx]) {
          toolParts[idx] = { ...toolParts[idx], result, isError };
        } else {
          toolParts.push({
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
      if (m.content) textParts.push({ type: "text", text: m.content });
      createdAt = m.timestamp;
    }
  }

  // One reasoning section, then one tool section, then the answer text.
  const parts: Array<
    { type: "text"; text: string } | { type: "reasoning"; text: string } | ToolPart
  > = [...reasoningParts, ...toolParts, ...textParts];

  if (parts.length === 0) parts.push({ type: "text", text: "" });

  // "running" is driven by the global stream flag for the last turn (per-item
  // `m.streaming` isn't set during the reasoning/tool phase). Live status label
  // drives the Reasoning trigger while running; cleared on completion.
  const running = streaming || !!isRunning;
  if (running && statusLabel) meta.statusLabel = statusLabel;

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
    status: { type: running ? "running" : "complete" } as never,
    content: parts as never,
    metadata: { custom: meta as unknown as Record<string, unknown> },
  };
}

/** Group jvchat's flat message stream into assistant-ui thread messages. */
export function buildThreadMessages(
  messages: Message[],
  isStreaming = false,
): ThreadMessageLike[] {
  // Index of the first message in the last assistant run (so we can mark that
  // turn as "running" when the thread is streaming).
  let lastAssistantStart = -1;
  for (let k = messages.length - 1; k >= 0; k--) {
    if (messages[k].role === "assistant") lastAssistantStart = k;
    else break;
  }

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
    const groupStart = i;
    const group: Message[] = [];
    while (i < messages.length && messages[i].role === "assistant") {
      group.push(messages[i]);
      i++;
    }
    if (group.length) {
      const isLastTurn = groupStart === lastAssistantStart;
      out.push(
        assistantGroupToThread(group, lastRootId, isStreaming && isLastTurn),
      );
    }
  }
  return out;
}
