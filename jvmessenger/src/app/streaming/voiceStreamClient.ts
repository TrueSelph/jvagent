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
 * Auth: mint a short-lived ticket via header-authed POST (keeps the long-lived
 * session token out of query strings), then open the socket with `?ticket=`.
 * Falls back to `?token=` when the ticket endpoint is unavailable (older servers).
 */

export interface LiveTranscriptionHandlers {
  /** Partial hypothesis for the current utterance (replaces the prior interim). */
  onInterim?: (text: string) => void;
  /** A stabilized segment (append to the committed transcript). */
  onFinal?: (text: string) => void;
  /** Fired once the socket is open and recording has started. */
  onReady?: () => void;
  /** Terminal failure after the stream has started. */
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

/** Dual-prefix WS URLs (``/api`` then bare), matching HTTP clients. */
export function wsUrls(
  agentUrl: string,
  agentId: string,
  credential: string,
  param: "ticket" | "token" = "ticket"
): string[] {
  const base = agentUrl.replace(/\/+$/, "");
  const wsBase = base.replace(/^http/i, "ws"); // http→ws, https→wss
  const q = `?${param}=${encodeURIComponent(credential)}`;
  const id = encodeURIComponent(agentId);
  return [
    `${wsBase}/api/agents/${id}/voice/stt/stream${q}`,
    `${wsBase}/agents/${id}/voice/stt/stream${q}`,
  ];
}

/** @deprecated Prefer {@link wsUrls}; kept for tests that assert a single URL. */
export function wsUrl(agentUrl: string, agentId: string, token: string): string {
  return wsUrls(agentUrl, agentId, token, "token")[0];
}

async function mintStreamTicket(
  agentUrl: string,
  agentId: string,
  sessionToken: string
): Promise<string | null> {
  const base = agentUrl.replace(/\/+$/, "");
  const urls = [
    `${base}/api/agents/${encodeURIComponent(agentId)}/voice/stt/stream/ticket`,
    `${base}/agents/${encodeURIComponent(agentId)}/voice/stt/stream/ticket`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Token": sessionToken,
        },
        body: "{}",
      });
      if (res.status === 404) continue;
      if (!res.ok) return null;
      const body = (await res.json()) as { ticket?: string };
      if (typeof body.ticket === "string" && body.ticket) return body.ticket;
      return null;
    } catch {
      /* try next prefix */
    }
  }
  return null;
}

/** Open the first WS URL that reaches OPEN; null if all fail before open. */
function connectFirstOpen(urls: string[]): Promise<WebSocket | null> {
  return new Promise((resolve) => {
    let idx = 0;
    const tryNext = () => {
      if (idx >= urls.length) {
        resolve(null);
        return;
      }
      const url = urls[idx++];
      let settled = false;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        tryNext();
        return;
      }
      ws.binaryType = "arraybuffer";
      const fail = () => {
        if (settled) return;
        settled = true;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        tryNext();
      };
      ws.onopen = () => {
        if (settled) return;
        settled = true;
        // Clear fail handlers so a later error doesn't open a second socket.
        ws.onerror = null;
        ws.onclose = null;
        resolve(ws);
      };
      ws.onerror = fail;
      ws.onclose = fail;
    };
    tryNext();
  });
}

/**
 * Begin live transcription. Resolves to a controller only after the server
 * sends ``ready`` (auth + provider ok), or ``null`` if streaming cannot start
 * (caller should fall back to batch ``transcribe``).
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

  // Prefer a short-lived ticket so the long-lived session token never appears
  // in WS query strings / access logs. Fall back to ?token= for older servers.
  const ticket = await mintStreamTicket(agentUrl, agentId, token);
  const urls = ticket
    ? wsUrls(agentUrl, agentId, ticket, "ticket")
    : wsUrls(agentUrl, agentId, token, "token");

  const ws = await connectFirstOpen(urls);
  if (!ws) {
    cleanupStream();
    return null;
  }

  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  let stopped = false;

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
    if (ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch {
        /* ignore */
      }
    }
    if (reason) handlers.onError?.(reason);
  };

  // Wait for server ``ready`` (or early ``error`` / close) before starting the
  // recorder or returning a controller — so MicButton can fall back to batch.
  const ready = await new Promise<boolean>((resolve) => {
    let done = false;
    const succeed = () => {
      if (done) return;
      done = true;
      resolve(true);
    };
    const fail = () => {
      if (done) return;
      done = true;
      resolve(false);
    };

    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return;
      let msg: { type?: string; transcript?: string; message?: string };
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "ready") {
        succeed();
        return;
      }
      if (msg.type === "error") {
        fail();
        return;
      }
      // Interim/final before ready is unexpected; ignore until after ready.
    };
    ws.onerror = () => fail();
    ws.onclose = () => fail();
  });

  if (!ready) {
    cleanupStream();
    try {
      ws.close();
    } catch {
      /* ignore */
    }
    return null;
  }

  try {
    recorder.start(250);
  } catch {
    cleanupStream();
    try {
      ws.close();
    } catch {
      /* ignore */
    }
    return null;
  }

  // Rebind message handler for the live phase.
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
    }
  };
  ws.onerror = () => {
    if (!stopped) finish("socket_error");
  };
  ws.onclose = () => {
    if (!stopped) finish();
  };

  handlers.onReady?.();

  return {
    stop: () => {
      finish();
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
