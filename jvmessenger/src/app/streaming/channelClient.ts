/**
 * Persistent session channel — how the agent reaches the visitor *between*
 * turns (proactive follow-ups, scheduled nudges, TaskMonitor output).
 *
 * The interact stream is turn-scoped: it closes when the walk ends, so between
 * turns the browser has no open ear. jvagent already exposes a long-lived
 * subscription (`POST /agents/{id}/reply/subscribe` with `stream: true`), which
 * accepts the same Mode B `X-Session-Token` the messenger already holds — so
 * this is a client, not a new endpoint.
 *
 * Uses `fetch` + `ReadableStream` rather than `EventSource`, which can neither
 * POST nor set headers. Frames are parsed with the interact client's
 * `parseSSEBuffer` (shared, deliberately not forked).
 *
 * Delivery caveats the caller must handle:
 * - The backlog is replayed on every (re)connect and is never drained by
 *   streaming subscribers, so **the caller must dedup by message id**.
 * - The same response bus feeds both this channel and the interact stream, so
 *   the caller should suspend the channel while a turn is running.
 */

import { parseSSEBuffer } from "./sseClient";
import type { ResponseMessageData, SSEChunk } from "./types";

export interface ChannelHandlers {
  /** A message arrived on the session channel (already parsed). */
  onMessage: (msg: ResponseMessageData) => void;
  /** Connection established (fired on every successful (re)connect). */
  onOpen?: () => void;
}

export interface ChannelController {
  /** Stop reconnecting and tear down the current connection. */
  close: () => void;
}

export interface ChannelOptions {
  agentUrl: string;
  agentId: string;
  sessionId: string;
  /** Current session token; read fresh on every reconnect. */
  getToken: () => string | undefined;
  /** Mint a fresh token after an auth failure. Return null to give up. */
  refreshToken?: () => Promise<string | null>;
  handlers: ChannelHandlers;
}

/** Reconnect backoff ladder (ms). Jitter is added per attempt. */
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 30000];
/** A connection alive this long resets the backoff ladder. */
const HEALTHY_AFTER_MS = 30000;

function backoffFor(attempt: number): number {
  const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
  return base + Math.floor(Math.random() * 400);
}

export function openSessionChannel(opts: ChannelOptions): ChannelController {
  const { agentUrl, agentId, sessionId, getToken, refreshToken, handlers } = opts;
  const base = agentUrl.replace(/\/+$/, "");
  let stopped = false;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let refreshedOnce = false;

  const urls = [
    `${base}/api/agents/${encodeURIComponent(agentId)}/reply/subscribe`,
    `${base}/agents/${encodeURIComponent(agentId)}/reply/subscribe`,
  ];

  /**
   * The subscribe stream frames each message **bare** — `stream_messages`
   * yields `format_sse_chunk(message.to_dict())`, so the frame *is* the
   * ResponseMessage, unlike the interact stream's `{type, message}` envelope.
   * Accept either shape so the two transports can share `parseSSEBuffer`.
   */
  const dispatch = (frame: SSEChunk & Partial<ResponseMessageData>) => {
    const enveloped = frame.message;
    if (enveloped && typeof enveloped !== "string") {
      handlers.onMessage(enveloped as ResponseMessageData);
      return;
    }
    if (frame.message_type) handlers.onMessage(frame as ResponseMessageData);
  };

  /** One connection attempt. Resolves when the stream ends for any reason. */
  const connectOnce = async (): Promise<void> => {
    const token = getToken();
    if (!token) return;

    controller = new AbortController();
    const startedAt = Date.now();

    for (const url of urls) {
      let res: Response;
      try {
        res = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Session-Token": token,
          },
          body: JSON.stringify({ session_id: sessionId, stream: true }),
          signal: controller.signal,
        });
      } catch {
        return; // network error — the caller's backoff handles it
      }

      if (res.status === 404) continue; // try the unprefixed route
      if (res.status === 401 || res.status === 403) {
        // Token likely expired while the tab sat idle. Try exactly one refresh.
        if (!refreshedOnce && refreshToken) {
          refreshedOnce = true;
          await refreshToken();
        }
        return;
      }
      if (!res.ok || !res.body) return;

      refreshedOnce = false;
      attempt = 0;
      handlers.onOpen?.();

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const [chunks, rest] = parseSSEBuffer(buffer);
          buffer = rest;
          for (const c of chunks) dispatch(c);
        }
      } catch {
        // Aborted or dropped — fall through to reconnect.
      } finally {
        try {
          reader.cancel();
        } catch {
          /* already closed */
        }
      }

      // A long-lived connection that finally dropped isn't a failing endpoint.
      if (Date.now() - startedAt > HEALTHY_AFTER_MS) attempt = 0;
      return;
    }
  };

  const loop = async (): Promise<void> => {
    while (!stopped) {
      await connectOnce();
      if (stopped) return;
      const delay = backoffFor(attempt++);
      await new Promise<void>((resolve) => {
        timer = setTimeout(resolve, delay);
      });
    }
  };

  void loop();

  return {
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
    },
  };
}
