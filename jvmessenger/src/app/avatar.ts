/**
 * Agent profile resolution for the messenger: avatar, name, and description.
 * Precedence for each field:
 *   1. the embed's `data-*` (explicit override)
 *   2. the agent's public profile, relayed by GET /agents/{id}/profile
 *   3. a built-in default (avatar only)
 */

// Built-in default avatar: a neutral assistant glyph (inline SVG data URI).
// Used when the embed sets no avatar and the agent has none.
export const DEFAULT_AVATAR =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">' +
      '<rect width="40" height="40" rx="20" fill="#18181b"/>' +
      '<g fill="none" stroke="#fafafa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="12" y="14" width="16" height="12" rx="3.5"/>' +
      '<path d="M20 10.5v3.5"/><circle cx="20" cy="9" r="1.4" fill="#fafafa" stroke="none"/>' +
      '<path d="M16.5 19.5v1.5M23.5 19.5v1.5"/>' +
      '<path d="M9.5 19v3M30.5 19v3"/>' +
      "</g></svg>"
  );

export interface AgentProfile {
  avatar: string | null;
  name: string | null;
  description: string | null;
}

/** Fetch the agent's public profile from the relay endpoint (best-effort). */
export async function fetchAgentProfile(
  agentUrl: string,
  agentId: string
): Promise<AgentProfile> {
  const empty: AgentProfile = { avatar: null, name: null, description: null };
  const base = agentUrl.replace(/\/+$/, "");
  const urls = [
    `${base}/api/agents/${agentId}/profile`,
    `${base}/agents/${agentId}/profile`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, { method: "GET" });
      if (res.status === 404) continue;
      if (!res.ok) return empty;
      const data = await res.json();
      const p = (data?.data ?? data) as Partial<AgentProfile>;
      return {
        avatar: typeof p.avatar === "string" && p.avatar ? p.avatar : null,
        name: typeof p.name === "string" && p.name ? p.name : null,
        description:
          typeof p.description === "string" && p.description
            ? p.description
            : null,
      };
    } catch {
      return empty;
    }
  }
  return empty;
}
