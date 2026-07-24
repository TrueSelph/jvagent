/**
 * Framework-agnostic client for the jvagent interact SSE stream.
 *
 * POSTs to ``{agentUrl}/api/agents/{agentId}/interact`` with ``stream:true`` and
 * parses the ``data:`` frames, invoking typed callbacks. Handles the ``/api``
 * prefix fallback (some deployments mount interact unprefixed) and echoes the
 * ``X-Session-Token`` when one is known (required once a conversation exists).
 */

import type { SSEChunk } from "./types";

export interface InteractRequest {
  utterance: string;
  channel?: string;
  user_id?: string;
  session_id?: string;
  data?: Record<string, unknown>;
}

export interface StreamHandlers {
  onStart?: (chunk: SSEChunk) => void;
  onMessage?: (chunk: SSEChunk) => void;
  onFinal?: (chunk: SSEChunk) => void;
  onError?: (message: string) => void;
}

export interface StreamOptions {
  agentUrl: string;
  agentId: string;
  request: InteractRequest;
  sessionToken?: string | null;
  signal?: AbortSignal;
}

/** Parse a raw SSE text buffer into complete frames, returning [chunks, rest]. */
export function parseSSEBuffer(buffer: string): [SSEChunk[], string] {
  const chunks: SSEChunk[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    for (const line of part.split("\n")) {
      const trimmed = line.trimStart();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload) continue;
      try {
        chunks.push(JSON.parse(payload) as SSEChunk);
      } catch {
        // Ignore malformed frames rather than aborting the whole stream.
      }
    }
  }
  return [chunks, rest];
}

function interactUrls(agentUrl: string, agentId: string): string[] {
  const base = agentUrl.replace(/\/+$/, "");
  return [
    `${base}/api/agents/${agentId}/interact`,
    `${base}/agents/${agentId}/interact`,
  ];
}

/**
 * Open the interact stream and dispatch frames to handlers until it ends.
 * Resolves when the stream closes; rejects only on a fatal transport error
 * (handlers receive stream-level ``error`` frames via ``onError``).
 */
export async function streamInteract(
  opts: StreamOptions,
  handlers: StreamHandlers
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (opts.sessionToken) headers["X-Session-Token"] = opts.sessionToken;

  const body = JSON.stringify({
    ...opts.request,
    channel: opts.request.channel ?? "default",
    stream: true,
  });

  const urls = interactUrls(opts.agentUrl, opts.agentId);
  let response: Response | null = null;
  let lastErr: unknown = null;
  for (const url of urls) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers,
        body,
        signal: opts.signal,
      });
      if (res.status === 404) {
        lastErr = new Error("404");
        continue; // try the next prefix variant
      }
      response = res;
      break;
    } catch (err) {
      lastErr = err;
    }
  }

  if (!response) {
    handlers.onError?.("Could not reach the agent.");
    throw lastErr instanceof Error ? lastErr : new Error("network error");
  }
  if (!response.ok || !response.body) {
    handlers.onError?.(`Request failed (${response.status}).`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const [chunks, rest] = parseSSEBuffer(buffer);
      buffer = rest;
      for (const chunk of chunks) dispatch(chunk, handlers);
    }
    // Flush any trailing complete frame.
    const [chunks] = parseSSEBuffer(buffer + "\n\n");
    for (const chunk of chunks) dispatch(chunk, handlers);
  } finally {
    reader.releaseLock();
  }
}

function dispatch(chunk: SSEChunk, handlers: StreamHandlers): void {
  switch (chunk.type) {
    case "start":
      handlers.onStart?.(chunk);
      break;
    case "message":
      handlers.onMessage?.(chunk);
      break;
    case "final":
      handlers.onFinal?.(chunk);
      break;
    case "error":
      handlers.onError?.(
        typeof chunk.message === "string" ? chunk.message : "Something went wrong."
      );
      break;
  }
}
