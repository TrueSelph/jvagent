/**
 * Messenger configuration: the resolved shape passed from the loader to the iframe
 * app, plus the parser that derives it from the embed script tag's `data-*`
 * attributes.
 *
 * Embed example:
 *   <script src="https://agent.host/messenger/loader.js"
 *           data-agent-url="https://agent.host"
 *           data-agent-id="n:Agent:123"
 *           data-avatar="https://acme.com/bot.png"
 *           data-theme="auto"
 *           data-greeting="Hi! How can I help?"
 *           data-quick-replies='["Track my order","Talk to a human"]'
 *           data-show-reasoning="false"
 *           data-attachments="true"
 *           data-voice="true"
 *           data-fullscreen="true"></script>
 */

/** Fully-resolved messenger configuration handed to the iframe app. */
export interface MessengerConfig {
  /** Agent server base URL the app calls for interact/voice/upload. */
  agentUrl: string;
  /** Target agent id (path param on every API call). */
  agentId: string;
  /** Agent avatar image URL (optional). */
  avatar?: string;
  /** Color theme. `auto` follows the host's prefers-color-scheme. */
  theme: "light" | "dark" | "auto";
  /** Header title / agent name shown in the popup. */
  title: string;
  /** Short agent description shown under the name in the header. */
  description?: string;
  /** Opening assistant message rendered before the first turn. */
  greeting?: string;
  /** Quick-reply chip labels offered up front. */
  quickReplies: string[];
  /** Optional info banner pinned above the thread (e.g. "responses may be slow"). */
  notice?: string;
  /** Optional data-use disclosure; when set, the user must Accept before chatting. */
  consent?: string;
  /** Reveal reasoning/tool-call rows (masked by default). */
  showReasoning: boolean;
  /** Enable the attachment upload affordance. */
  attachments: boolean;
  /** Enable STT (mic) + TTS (speak) controls. */
  voice: boolean;
  /** Allow expanding the popup to fullscreen. */
  fullscreen: boolean;
  /** Play a subtle chime when an assistant message arrives. */
  sound: boolean;
  /**
   * Greeting shown in the launcher teaser card before the panel is opened
   * (with an inline mini-composer). Empty disables the teaser entirely.
   */
  teaser?: string;
  /** Delay in ms before the teaser appears. */
  teaserDelay: number;
  /** Days a dismissal suppresses the teaser for (0 = only this page view). */
  teaserCooldownDays: number;
}

/** Defaults applied when a `data-*` attribute is absent. */
export const CONFIG_DEFAULTS: Omit<MessengerConfig, "agentUrl" | "agentId"> = {
  avatar: undefined,
  theme: "auto",
  title: "Chat",
  description: undefined,
  greeting: undefined,
  quickReplies: [],
  notice: undefined,
  consent: undefined,
  showReasoning: false,
  attachments: false,
  voice: false,
  fullscreen: true,
  sound: true,
  teaser: undefined,
  teaserDelay: 4000,
  teaserCooldownDays: 7,
};

function asInt(value: string | undefined, fallback: number): number {
  const n = Number.parseInt((value ?? "").trim(), 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

function asBool(value: string | undefined, fallback: boolean): boolean {
  if (value == null) return fallback;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function asStringArray(value: string | undefined): string[] {
  if (!value) return [];
  const trimmed = value.trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed.map((x) => String(x)).filter(Boolean);
    }
  } catch {
    // Fall back to a comma-separated list when it isn't valid JSON.
    return trimmed
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

function asTheme(value: string | undefined): MessengerConfig["theme"] {
  const v = (value || "").trim().toLowerCase();
  return v === "light" || v === "dark" ? v : "auto";
}

/**
 * Build a {@link MessengerConfig} from a camelCase source map. The loader merges
 * the embed script's `data-*` attributes with any query params on the loader
 * URL (`loader.js?agentId=...&agentUrl=...`), so an agent can be bound either
 * way. Throws when the required `agentUrl` / `agentId` are missing so the loader
 * can fail loudly rather than silently no-op.
 */
export function parseConfig(
  dataset: Record<string, string | undefined>
): MessengerConfig {
  const agentUrl = (dataset.agentUrl || "").trim();
  const agentId = (dataset.agentId || "").trim();
  if (!agentUrl || !agentId) {
    throw new Error(
      "[jvmessenger] data-agent-url and data-agent-id are required on the embed <script> tag"
    );
  }
  return {
    agentUrl: agentUrl.replace(/\/+$/, ""),
    agentId,
    avatar: dataset.avatar?.trim() || CONFIG_DEFAULTS.avatar,
    theme: asTheme(dataset.theme),
    title: dataset.title?.trim() || CONFIG_DEFAULTS.title,
    description: dataset.description?.trim() || CONFIG_DEFAULTS.description,
    greeting: dataset.greeting?.trim() || CONFIG_DEFAULTS.greeting,
    quickReplies: asStringArray(dataset.quickReplies),
    notice: dataset.notice?.trim() || CONFIG_DEFAULTS.notice,
    consent: dataset.consent?.trim() || CONFIG_DEFAULTS.consent,
    showReasoning: asBool(dataset.showReasoning, CONFIG_DEFAULTS.showReasoning),
    attachments: asBool(dataset.attachments, CONFIG_DEFAULTS.attachments),
    voice: asBool(dataset.voice, CONFIG_DEFAULTS.voice),
    fullscreen: asBool(dataset.fullscreen, CONFIG_DEFAULTS.fullscreen),
    sound: asBool(dataset.sound, CONFIG_DEFAULTS.sound),
    teaser: dataset.teaser?.trim() || CONFIG_DEFAULTS.teaser,
    teaserDelay: asInt(dataset.teaserDelay, CONFIG_DEFAULTS.teaserDelay),
    teaserCooldownDays: asInt(
      dataset.teaserCooldownDays,
      CONFIG_DEFAULTS.teaserCooldownDays
    ),
  };
}
