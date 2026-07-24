/**
 * Per-agent session persistence for the anonymous messenger. Stores the
 * server-issued ``session_id`` (to resume a conversation) and ``session_token``
 * (the Mode B capability token resent as ``X-Session-Token``) in localStorage,
 * and refreshes the token before it expires via the interact refresh endpoint.
 */

export interface SessionState {
  sessionId?: string;
  userId?: string;
  sessionToken?: string;
}

function key(agentId: string): string {
  return `jvmessenger:session:${agentId}`;
}

export function loadSession(agentId: string): SessionState {
  try {
    const raw = localStorage.getItem(key(agentId));
    return raw ? (JSON.parse(raw) as SessionState) : {};
  } catch {
    return {};
  }
}

export function saveSession(agentId: string, state: SessionState): void {
  try {
    localStorage.setItem(key(agentId), JSON.stringify(state));
  } catch {
    // Storage may be unavailable (private mode); degrade to in-memory only.
  }
}

export function clearSession(agentId: string): void {
  try {
    localStorage.removeItem(key(agentId));
  } catch {
    // ignore
  }
}

// --- Chat history persistence (client-side, so a refresh keeps the thread) ---

const MAX_PERSISTED_MESSAGES = 100;

function historyKey(agentId: string): string {
  return `jvmessenger:history:${agentId}`;
}

interface StoredHistory {
  sessionId?: string;
  messages: unknown[];
}

/**
 * Load persisted messages for an agent, but only when they belong to the
 * currently-active session — so a new/rotated session starts with a clean
 * thread rather than showing a stale one.
 */
export function loadHistory(agentId: string, sessionId?: string): unknown[] {
  try {
    const raw = localStorage.getItem(historyKey(agentId));
    if (!raw) return [];
    const stored = JSON.parse(raw) as StoredHistory;
    if (!sessionId || stored.sessionId !== sessionId) return [];
    return Array.isArray(stored.messages) ? stored.messages : [];
  } catch {
    return [];
  }
}

export function saveHistory(
  agentId: string,
  sessionId: string | undefined,
  messages: unknown[]
): void {
  try {
    const trimmed = messages.slice(-MAX_PERSISTED_MESSAGES);
    localStorage.setItem(
      historyKey(agentId),
      JSON.stringify({ sessionId, messages: trimmed })
    );
  } catch {
    // Storage may be unavailable / full; degrade to in-memory only.
  }
}

export function clearHistory(agentId: string): void {
  try {
    localStorage.removeItem(historyKey(agentId));
  } catch {
    // ignore
  }
}

// --- Consent acceptance (per agent, keyed to the disclosure text) ---

function consentKey(agentId: string): string {
  return `jvmessenger:consent:${agentId}`;
}

/** Cheap stable hash so re-wording the disclosure re-prompts. */
function hashText(text: string): string {
  let h = 0;
  for (let i = 0; i < text.length; i++) {
    h = (h << 5) - h + text.charCodeAt(i);
    h |= 0;
  }
  return String(h);
}

export function hasAcceptedConsent(agentId: string, consentText: string): boolean {
  try {
    return localStorage.getItem(consentKey(agentId)) === hashText(consentText);
  } catch {
    return false;
  }
}

export function acceptConsent(agentId: string, consentText: string): void {
  try {
    localStorage.setItem(consentKey(agentId), hashText(consentText));
  } catch {
    // ignore
  }
}

/**
 * Exchange the current session token for a fresh one. Best-effort: returns the
 * new token on success or null on failure (the caller keeps using the old one
 * until the next turn, which will surface any hard expiry).
 */
export async function refreshSessionToken(
  agentUrl: string,
  agentId: string,
  sessionToken: string
): Promise<string | null> {
  const base = agentUrl.replace(/\/+$/, "");
  const urls = [
    `${base}/api/agents/${agentId}/interact/session/refresh`,
    `${base}/agents/${agentId}/interact/session/refresh`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Token": sessionToken,
        },
        body: JSON.stringify({}),
      });
      if (res.status === 404) continue;
      if (!res.ok) return null;
      const data = await res.json();
      const token =
        data?.session_token ?? data?.data?.session_token ?? null;
      return typeof token === "string" ? token : null;
    } catch {
      return null;
    }
  }
  return null;
}
