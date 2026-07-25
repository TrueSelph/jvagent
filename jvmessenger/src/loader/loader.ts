/**
 * jvmessenger loader — the single entry the customer embeds:
 *
 *   <script src="https://agent.host/messenger/loader.js" data-agent-url=... data-agent-id=...></script>
 *
 * Framework-free (no React) so it stays tiny and safe to run in any host page.
 * It reads its own `data-*` config, injects a Shadow-DOM launcher button, and
 * lazily creates the chat iframe on first open. The iframe is served from the
 * same origin as this script; the agent API URL is whatever `data-agent-url`
 * points at and is handed to the app over the postMessage handshake.
 */

import { parseConfig } from "../shared/config";
import { createHostBridge } from "./bridge-host";
import { createLauncher } from "./launcher";

function boot(): void {
  // `document.currentScript` is valid while this IIFE executes synchronously.
  const script = document.currentScript as HTMLScriptElement | null;
  if (!script) {
    console.error("[jvmessenger] could not locate the embed <script> element");
    return;
  }
  // Guard against double-embed.
  if ((window as unknown as { __jvmessengerLoaded?: boolean }).__jvmessengerLoaded) {
    return;
  }
  (window as unknown as { __jvmessengerLoaded?: boolean }).__jvmessengerLoaded = true;

  // Merge the embed script's data-* attributes with any query params on the
  // loader URL (loader.js?agentId=...&agentUrl=...), so an agent can be bound
  // via URL params. Query params override data-* (explicit URL binding wins).
  const source: Record<string, string | undefined> = { ...script.dataset };
  try {
    const u = new URL(script.src, window.location.href);
    u.searchParams.forEach((value, rawKey) => {
      const key = rawKey.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      source[key] = value;
    });
  } catch {
    // Malformed src — fall back to data-* only.
  }

  let config;
  try {
    config = parseConfig(source);
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    return;
  }

  const messengerOrigin = new URL(script.src, window.location.href).origin;
  const iframeSrc = `${messengerOrigin}/app.html`;

  // Teaser dismissal is remembered on the *host* origin (the loader runs there),
  // so a visitor who waves us away isn't nagged on every page view.
  const TEASER_KEY = `jvmessenger:teaser-dismissed:${config.agentId}`;
  const teaserSuppressed = (): boolean => {
    try {
      const until = Number(window.localStorage.getItem(TEASER_KEY) || 0);
      return Number.isFinite(until) && Date.now() < until;
    } catch {
      return false;
    }
  };
  const suppressTeaser = (): void => {
    try {
      const ms = config.teaserCooldownDays * 24 * 60 * 60 * 1000;
      window.localStorage.setItem(TEASER_KEY, String(Date.now() + ms));
    } catch {
      // Storage unavailable (private mode) — dismissal lasts this page view.
    }
  };

  const openChat = (): void => {
    bridge.open();
    launcher.setOpen(true);
    launcher.setUnread(0);
  };

  const launcher = createLauncher({
    avatar: config.avatar,
    title: config.title,
    teaser: config.teaser,
    onToggle: () => {
      if (bridge.isOpen()) {
        bridge.close();
        launcher.setOpen(false);
      } else {
        openChat();
      }
    },
    onTeaserSend: (text) => {
      // Open the panel and hand the typed text to the app to send as turn one.
      suppressTeaser();
      openChat();
      bridge.prefill(text);
    },
    onTeaserDismiss: suppressTeaser,
  });

  // Show the teaser after a beat, but never over an already-open panel.
  if (config.teaser && !teaserSuppressed()) {
    window.setTimeout(() => {
      if (!bridge.isOpen()) launcher.showTeaser();
    }, config.teaserDelay);
  }

  const bridge = createHostBridge({
    mount: launcher.mount,
    iframeSrc,
    iframeOrigin: messengerOrigin,
    config,
    onClose: () => launcher.setOpen(false),
    onNotify: (unread) => {
      if (!bridge.isOpen()) launcher.setUnread(unread);
    },
  });
}

if (document.readyState === "loading") {
  // currentScript is only reliable synchronously; capture it now.
  boot();
} else {
  boot();
}
