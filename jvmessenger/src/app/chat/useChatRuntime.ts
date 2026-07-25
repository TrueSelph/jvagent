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
  type MessageAction,
  type ResponseMessageData,
  type SSEChunk,
} from "../streaming/types";
import {
  answerText,
  emptyTurn,
  hasAnswer,
  reduceMessage,
  withError,
  type ActivityEntry,
  type TurnState,
} from "../streaming/reducer";
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
  turn: TurnState,
  showReasoning: boolean
): ThreadMessageLike["content"] {
  const reason =
    showReasoning && turn.reasoning.trim()
      ? [{ type: "reasoning" as const, text: turn.reasoning }]
      : [];
  return [...reason, { type: "text" as const, text: answerText(turn) }];
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
  // Live tool/status progress for the in-flight turn. Ephemeral by design — it
  // is never persisted to history.
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  // Ephemeral failure banner for the last turn (kept out of the message list).
  const [turnError, setTurnError] = useState<string | null>(null);
  // Aborts the in-flight stream when the user hits Stop.
  const abortRef = useRef<AbortController | null>(null);
  // Last user utterance + attachments, so regenerate can replay the turn.
  const lastTurnRef = useRef<{ text: string; attachments: UploadedAttachment[] } | null>(
    null
  );
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
      // Clear last turn's suggestions + error banner; this turn's are collected below.
      setSuggestions([]);
      setTurnError(null);
      setActivity([]);
      lastTurnRef.current = { text: userText, attachments: turnAttachments };

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

      // The whole turn folds into this pure state (see streaming/reducer.ts).
      let turn = emptyTurn();
      const update = () =>
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: assistantParts(turn, config.showReasoning) }
              : m
          )
        );

      const onMessage = (chunk: SSEChunk) => {
        const data = chunk.message;
        if (!data || typeof data === "string") return;
        const next = reduceMessage(turn, data as ResponseMessageData);
        if (next === turn) return; // nothing changed — skip the re-render
        turn = next;
        setActivity(turn.activity);
        update();
      };

      // Real cancellation: sseClient honours `signal`, but nothing ever passed one.
      const controller = new AbortController();
      abortRef.current = controller;

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
            signal: controller.signal,
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
              turn = withError(turn, text);
              update();
            },
          }
        );
        // Subtle chime once the assistant reply has actually landed.
        if (config.sound && hasAnswer(turn) && !turn.error) playChime();
      } catch (err) {
        // A user-initiated abort is not an error — keep whatever streamed.
        const aborted =
          controller.signal.aborted ||
          (err instanceof DOMException && err.name === "AbortError");
        if (!aborted) {
          turn = withError(turn, "Connection error. Please try again.");
        }
        update();
      } finally {
        abortRef.current = null;
        setIsRunning(false);
        setActivity([]);
        setSuggestions(turn.suggestions);
        // Errors are surfaced as an ephemeral banner, never as assistant content:
        // otherwise they persist into history and get read aloud by TTS.
        setTurnError(turn.error ?? null);
        if (turn.error && !hasAnswer(turn)) {
          // Nothing streamed — drop the empty bubble rather than leaving a blank.
          setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        }
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

  /** Abort the in-flight stream (the Stop button). */
  const onCancel = useCallback(async () => {
    abortRef.current?.abort();
  }, []);

  /**
   * Regenerate: drop the last assistant turn and replay the preceding user
   * message. `ActionBarPrimitive.Reload` was rendered but inert without this.
   */
  const onReload = useCallback(async () => {
    const last = lastTurnRef.current;
    if (!last || isRunning) return;
    setMessages((prev) => {
      const idx = prev.map((m) => m.role).lastIndexOf("user");
      return idx === -1 ? prev : prev.slice(0, idx);
    });
    setAttachments(last.attachments);
    await runTurn(last.text);
  }, [isRunning, runTurn]);

  /** Retry after a failed turn (surfaced on the error banner). */
  const retry = useCallback(async () => {
    const last = lastTurnRef.current;
    if (!last || isRunning) return;
    setTurnError(null);
    setAttachments(last.attachments);
    await runTurn(last.text);
  }, [isRunning, runTurn]);

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning,
    onNew,
    onCancel,
    onReload,
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
      activity,
      turnError,
      retry,
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
      activity,
      turnError,
      retry,
      reset,
      hasUserMessage,
      downloadTranscript,
    ]
  );
}
