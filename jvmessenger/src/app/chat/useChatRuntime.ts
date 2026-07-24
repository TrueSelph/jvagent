/**
 * assistant-ui ExternalStore runtime wired to the jvagent interact SSE stream.
 *
 * Owns the ThreadMessageLike list, drives a turn on new input (composer or quick
 * reply), and applies the **masking** rule: rows with ``category:"thought"``
 * (reasoning / tool_call / tool_result / status) are hidden by default and only
 * surfaced as a reasoning part when ``config.showReasoning`` is on. Captures the
 * server-issued session id + token on ``start`` and persists them for resume.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { MessengerConfig } from "../../shared/config";
import { streamInteract } from "../streaming/sseClient";
import {
  extractSuggestions,
  type MessageAction,
  type ResponseMessageData,
  type SSEChunk,
} from "../streaming/types";
import {
  clearHistory,
  clearSession,
  loadHistory,
  loadSession,
  refreshSessionToken,
  saveHistory,
  saveSession,
  type SessionState,
} from "../streaming/session";
import type { UploadedAttachment } from "../streaming/uploadClient";
import { playChime, primeAudio } from "../streaming/sound";

function attachmentsToData(
  pending: UploadedAttachment[]
): Record<string, unknown> | undefined {
  if (!pending.length) return undefined;
  const toEntry = (a: UploadedAttachment) => ({
    url: a.url,
    mime_type: a.mime_type,
    filename: a.filename,
  });
  const images = pending.filter((a) => a.mime_type.startsWith("image/"));
  const files = pending.filter((a) => !a.mime_type.startsWith("image/"));
  const data: Record<string, unknown> = {};
  if (images.length) data.image_urls = images.map(toEntry);
  if (files.length) data.files = files.map(toEntry);
  return data;
}

let _id = 0;
// Include a random suffix so ids don't collide with those restored from a
// previous session after a page reload (the counter resets on reload).
const nextId = () => `m${++_id}_${Math.random().toString(36).slice(2, 8)}`;

function extractText(message: AppendMessage): string {
  return message.content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("")
    .trim();
}

function assistantParts(
  answer: string,
  reasoning: string,
  showReasoning: boolean
): ThreadMessageLike["content"] {
  const reason =
    showReasoning && reasoning.trim()
      ? [{ type: "reasoning" as const, text: reasoning }]
      : [];
  return [...reason, { type: "text" as const, text: answer }];
}

export function useChatRuntime(config: MessengerConfig) {
  const session = useRef<SessionState>(loadSession(config.agentId));
  // Restore prior messages on mount, but only when they belong to the still-active
  // session (else start clean). Greeting stays on the welcome screen, so an empty
  // thread shows it.
  const [messages, setMessages] = useState<ThreadMessageLike[]>(
    () =>
      loadHistory(config.agentId, session.current.sessionId) as ThreadMessageLike[]
  );
  const [isRunning, setIsRunning] = useState(false);
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  // Agent-driven follow-up chips from the last turn's message metadata.
  const [suggestions, setSuggestions] = useState<MessageAction[]>([]);
  const getToken = useCallback(() => session.current.sessionToken, []);

  // Persist the thread whenever it changes so a page refresh keeps the history.
  useEffect(() => {
    if (messages.length) {
      saveHistory(config.agentId, session.current.sessionId, messages);
    }
  }, [config.agentId, messages]);

  const runTurn = useCallback(
    async (userText: string) => {
      if (isRunning) return;

      // Snapshot any pending uploads for this turn.
      const turnAttachments = attachments;
      const trimmed = userText.trim();
      // Allow attachment-only turns (image with no typed text): fall back to the
      // filenames as the visible/utterance text so the vision reflex still fires.
      if (!trimmed && !turnAttachments.length) return;
      // Unlock the audio context now, while we're still inside the send gesture,
      // so the reply chime can play later (autoplay policy).
      if (config.sound) primeAudio();
      const effectiveText =
        trimmed || turnAttachments.map((a) => a.filename).join(", ");
      if (turnAttachments.length) setAttachments([]);
      // Clear last turn's suggestions; collect this turn's below.
      setSuggestions([]);
      let turnSuggestions: MessageAction[] = [];

      // Render the sent attachments in the bubble: images as thumbnails, other
      // files as chips (assistant-ui image/file parts). The filename fallback is
      // only used as the utterance text when nothing visual is shown.
      const attachmentParts = turnAttachments.map((a) =>
        a.mime_type.startsWith("image/")
          ? ({ type: "image" as const, image: a.url })
          : ({
              type: "file" as const,
              data: a.url,
              mimeType: a.mime_type,
              filename: a.filename,
            })
      );
      const displayParts = [
        ...attachmentParts,
        ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
      ];
      const userMsg: ThreadMessageLike = {
        id: nextId(),
        role: "user",
        content: displayParts.length
          ? displayParts
          : [{ type: "text", text: effectiveText }],
      };
      const assistantId = nextId();
      setMessages((prev) => [
        ...prev,
        userMsg,
        { id: assistantId, role: "assistant", content: [{ type: "text", text: "" }] },
      ]);
      setIsRunning(true);

      let answer = "";
      let reasoning = "";
      const update = () =>
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: assistantParts(answer, reasoning, config.showReasoning),
                }
              : m
          )
        );

      const onMessage = (chunk: SSEChunk) => {
        const data = chunk.message;
        if (!data || typeof data === "string") return;
        const msg = data as ResponseMessageData;
        if (msg.category === "thought") {
          // MASKED by default: only accumulate when reasoning is revealed.
          if (config.showReasoning && msg.content) {
            reasoning += (reasoning ? "\n" : "") + msg.content;
            update();
          }
          return;
        }
        // Agent-driven follow-up chips ride on message metadata.
        const s = extractSuggestions(msg.metadata);
        if (s.length) turnSuggestions = s;
        // category "user" → the visible answer.
        if (msg.message_type === "stream_chunk" || msg.message_type === "adhoc") {
          answer += msg.content ?? "";
        } else if (!answer) {
          answer = msg.content ?? "";
        }
        update();
      };

      try {
        await streamInteract(
          {
            agentUrl: config.agentUrl,
            agentId: config.agentId,
            sessionToken: session.current.sessionToken,
            request: {
              utterance: effectiveText,
              user_id: session.current.userId,
              session_id: session.current.sessionId,
              data: attachmentsToData(turnAttachments),
            },
          },
          {
            onStart: (chunk) => {
              session.current = {
                sessionId: chunk.session_id ?? session.current.sessionId,
                userId: chunk.user_id ?? session.current.userId,
                sessionToken: chunk.session_token ?? session.current.sessionToken,
              };
              saveSession(config.agentId, session.current);
            },
            onMessage,
            onError: (text) => {
              answer = answer || `⚠️ ${text}`;
              update();
            },
          }
        );
        // Subtle chime once the assistant reply has landed (skip on error).
        if (config.sound && answer && !answer.startsWith("⚠️")) playChime();
      } catch {
        answer = answer || "⚠️ Connection error. Please try again.";
        update();
      } finally {
        setIsRunning(false);
        setSuggestions(turnSuggestions);
        // Proactively refresh the token for the next turn.
        const tok = session.current.sessionToken;
        if (tok) {
          refreshSessionToken(config.agentUrl, config.agentId, tok).then((fresh) => {
            if (fresh) {
              session.current = { ...session.current, sessionToken: fresh };
              saveSession(config.agentId, session.current);
            }
          });
        }
      }
    },
    [config, isRunning, attachments]
  );

  const onNew = useCallback(
    async (message: AppendMessage) => {
      await runTurn(extractText(message));
    },
    [runTurn]
  );

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning,
    onNew,
    convertMessage: (m: ThreadMessageLike) => m,
  });

  const addAttachment = useCallback(
    (a: UploadedAttachment) => setAttachments((prev) => [...prev, a]),
    []
  );
  const removeAttachment = useCallback(
    (url: string) =>
      setAttachments((prev) => prev.filter((a) => a.url !== url)),
    []
  );

  // Clean slate: drop persisted history + session, clear the thread + chips.
  const reset = useCallback(() => {
    clearHistory(config.agentId);
    clearSession(config.agentId);
    session.current = {};
    setAttachments([]);
    setSuggestions([]);
    setMessages([]);
  }, [config.agentId]);

  const hasUserMessage = messages.some((m) => m.role === "user");

  // Build a plain-text transcript of the local thread and download it.
  const downloadTranscript = useCallback(() => {
    if (!messages.length) return;
    const partsToText = (content: unknown): string => {
      if (typeof content === "string") return content;
      if (!Array.isArray(content)) return "";
      return content
        .map((raw) => {
          const p = raw as { type?: string; text?: string; filename?: string };
          if (p.type === "text") return p.text ?? "";
          if (p.type === "image") return "[image]";
          if (p.type === "file") return `[file: ${p.filename ?? "attachment"}]`;
          return "";
        })
        .filter(Boolean)
        .join(" ");
    };
    const body = messages
      .map((m) => ({
        who: m.role === "user" ? "You" : "Assistant",
        text: partsToText(m.content).trim(),
      }))
      .filter((l) => l.text)
      .map((l) => `${l.who}: ${l.text}`)
      .join("\n\n");
    if (!body) return;
    const header = `Chat transcript — ${config.title}\n${new Date().toString()}\n\n`;
    const blob = new Blob([header + body + "\n"], {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat-transcript-${new Date().toISOString().slice(0, 10)}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [messages, config.title]);

  return useMemo(
    () => ({
      runtime,
      sendText: runTurn,
      getToken,
      attachments,
      addAttachment,
      removeAttachment,
      suggestions,
      reset,
      hasUserMessage,
      downloadTranscript,
    }),
    [
      runtime,
      runTurn,
      getToken,
      attachments,
      addAttachment,
      removeAttachment,
      suggestions,
      reset,
      hasUserMessage,
      downloadTranscript,
    ]
  );
}
