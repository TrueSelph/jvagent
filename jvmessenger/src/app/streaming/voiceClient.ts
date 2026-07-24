/**
 * Clients for the public messenger voice endpoints. Both send the session token as
 * ``X-Session-Token`` (required by the server) and fall back from the ``/api``
 * prefix to the unprefixed route.
 */

async function postJson(
  agentUrl: string,
  agentId: string,
  suffix: string,
  token: string,
  body: unknown
): Promise<Record<string, unknown> | null> {
  const base = agentUrl.replace(/\/+$/, "");
  const urls = [
    `${base}/api/agents/${agentId}/${suffix}`,
    `${base}/agents/${agentId}/${suffix}`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Token": token,
        },
        body: JSON.stringify(body),
      });
      if (res.status === 404) continue;
      if (!res.ok) return null;
      const data = await res.json();
      return (data?.data ?? data) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
  return null;
}

/** Transcribe a recorded clip (base64) → text, or "" on failure. */
export async function transcribe(
  agentUrl: string,
  agentId: string,
  token: string,
  audioBase64: string,
  audioType: string
): Promise<string> {
  const out = await postJson(agentUrl, agentId, "voice/stt", token, {
    audio_base64: audioBase64,
    audio_type: audioType,
  });
  const t = out?.transcript;
  return typeof t === "string" ? t : "";
}

/** Synthesize speech for text → {audioBase64, mimeType}, or null on failure. */
export async function synthesize(
  agentUrl: string,
  agentId: string,
  token: string,
  text: string
): Promise<{ audioBase64: string; mimeType: string } | null> {
  const out = await postJson(agentUrl, agentId, "voice/tts", token, { text });
  const audio = out?.audio_base64;
  if (typeof audio !== "string" || !audio) return null;
  const mime = typeof out?.mime_type === "string" ? out.mime_type : "audio/mpeg";
  return { audioBase64: audio, mimeType: mime };
}
