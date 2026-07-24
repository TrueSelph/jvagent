/**
 * Real-time STT client: streams mic audio to the messenger's live-transcription
 * WebSocket and surfaces interim + final transcripts as the user speaks.
 *
 * Wire protocol (this client ⇄ jvagent `/agents/{id}/voice/stt/stream`):
 *   client → server : binary frames = raw MediaRecorder webm/opus chunks;
 *                      a text frame `{"type":"stop"}` signals end-of-audio.
 *   server → client : text JSON frames —
 *                      {"type":"ready"} | {"type":"interim","transcript":…}
 *                      {"type":"final","transcript":…} | {"type":"utterance_end"}
 *                      {"type":"error","message":…}
 *
 * The session token rides as a query param because browsers cannot set custom
 * headers on a WebSocket handshake (the server also gates it the same way the
 * voice/upload POST endpoints gate `X-Session-Token`).
 */

export interface LiveTranscriptionHandlers {
  /** Partial hypothesis for the current utterance (replaces the prior interim). */
  onInterim?: (text: string) => void;
  /** A stabilized segment (append to the committed transcript). */
  onFinal?: (text: string) => void;
  /** Fired once the socket is open and recording has started. */
  onReady?: () => void;
  /** Terminal failure; the caller should fall back to batch STT. */
  onError?: (reason: string) => void;
}

export interface LiveTranscriptionController {
  /** Stop recording, flush the last audio, and close the socket. */
  stop: () => void;
}

/** True when this browser can capture mic audio as a webm/opus stream. */
export function liveSttSupported(): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    (MediaRecorder.isTypeSupported?.("audio/webm;codecs=opus") ||
      MediaRecorder.isTypeSupported?.("audio/webm") ||
      false)
  );
}

function pickMimeType(): string | undefined {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]) {
    if (MediaRecorder.isTypeSupported?.(t)) return t;
  }
  return undefined;
}

/** Derive the STT-stream WebSocket URL (http→ws, https→wss; token in query). */
export function wsUrl(agentUrl: string, agentId: string, token: string): string {
  const base = agentUrl.replace(/\/+$/, "");
  const wsBase = base.replace(/^http/i, "ws"); // http→ws, https→wss
  const q = `?token=${encodeURIComponent(token)}`;
  return `${wsBase}/api/agents/${encodeURIComponent(agentId)}/voice/stt/stream${q}`;
}

/**
 * Begin live transcription. Resolves to a controller once the mic + socket are
 * up, or `null` if the environment/permissions/socket prevent streaming (the
 * caller should then fall back to the batch `transcribe` path).
 */
export async function startLiveTranscription(
  agentUrl: string,
  agentId: string,
  token: string,
  handlers: LiveTranscriptionHandlers
): Promise<LiveTranscriptionController | null> {
  if (!liveSttSupported()) return null;

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return null;
  }

  const cleanupStream = () => stream.getTracks().forEach((t) => t.stop());

  let ws: WebSocket;
  try {
    ws = new WebSocket(wsUrl(agentUrl, agentId, token));
  } catch {
    cleanupStream();
    return null;
  }
  ws.binaryType = "arraybuffer";

  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  let stopped = false;
  let opened = false;

  recorder.ondataavailable = (e) => {
    if (e.data.size && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };

  const finish = (reason?: string) => {
    if (stopped) return;
    stopped = true;
    try {
      if (recorder.state !== "inactive") recorder.stop();
    } catch {
      /* already stopped */
    }
    cleanupStream();
    // Tell the server to flush + close (only if the socket is still usable).
    if (ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch {
        /* ignore */
      }
    }
    if (reason) handlers.onError?.(reason);
  };

  ws.onopen = () => {
    opened = true;
    // 250 ms slices keep interim latency low without flooding the socket.
    try {
      recorder.start(250);
    } catch {
      finish("recorder_start_failed");
      return;
    }
    handlers.onReady?.();
  };

  ws.onmessage = (e) => {
    if (typeof e.data !== "string") return;
    let msg: { type?: string; transcript?: string; message?: string };
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    switch (msg.type) {
      case "interim":
        if (msg.transcript) handlers.onInterim?.(msg.transcript);
        break;
      case "final":
        if (msg.transcript) handlers.onFinal?.(msg.transcript);
        break;
      case "error":
        finish(msg.message || "stream_error");
        break;
      // "ready" / "utterance_end" need no client action here.
    }
  };

  ws.onerror = () => {
    // If the socket never opened, signal failure so the caller can fall back.
    if (!opened) {
      cleanupStream();
      handlers.onError?.("socket_error");
    }
  };

  ws.onclose = () => {
    if (!stopped) finish();
  };

  return {
    stop: () => {
      finish();
      // Give the last audio slice + stop control a moment to flush, then close.
      setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          try {
            ws.close();
          } catch {
            /* ignore */
          }
        }
      }, 400);
    },
  };
}
