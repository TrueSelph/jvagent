/**
 * Host side of the loader ↔ iframe bridge. Creates and styles the chat iframe,
 * runs the origin-checked postMessage handshake, and applies resize/close
 * requests from the app to the iframe container.
 */

import type { MessengerConfig } from "../shared/config";
import {
  type IframeToHost,
  type MessengerMode,
  envelope,
  readEnvelope,
} from "../shared/protocol";

export interface HostBridge {
  open(): void;
  close(): void;
  isOpen(): boolean;
  destroy(): void;
}

const PANEL_CSS: Partial<CSSStyleDeclaration> = {
  position: "fixed",
  // Reset top/left in case we're returning from fullscreen (inset:0), otherwise
  // the panel stays pinned to the top-left corner.
  top: "auto",
  left: "auto",
  bottom: "88px",
  right: "20px",
  width: "min(400px, calc(100vw - 40px))",
  // Cap height to the viewport minus the launcher + top gap so the top of the
  // panel is never clipped by the browser chrome. `dvh` tracks mobile toolbars.
  height: "min(600px, calc(100dvh - 108px))",
  maxHeight: "calc(100dvh - 108px)",
  border: "none",
  borderRadius: "16px",
  // Soft, light shadow only — no heavy/black framing around the popup.
  boxShadow: "0 6px 24px rgba(0,0,0,0.12), 0 1px 4px rgba(0,0,0,0.06)",
  zIndex: "2147483001",
  background: "transparent",
  colorScheme: "normal",
};

const FULLSCREEN_CSS: Partial<CSSStyleDeclaration> = {
  position: "fixed",
  inset: "0",
  width: "100vw",
  height: "100dvh",
  // Clear the panel's height caps, else the iframe stops short of the bottom.
  maxWidth: "none",
  maxHeight: "none",
  border: "none",
  borderRadius: "0",
  boxShadow: "none",
  zIndex: "2147483001",
  background: "transparent",
};

export function createHostBridge(opts: {
  mount: HTMLElement;
  iframeSrc: string;
  /** Origin the iframe is served from — the only accepted message origin. */
  iframeOrigin: string;
  config: MessengerConfig;
  onResize?: (mode: MessengerMode) => void;
  onClose?: () => void;
  onNotify?: (unread: number) => void;
}): HostBridge {
  let iframe: HTMLIFrameElement | null = null;
  let open = false;

  const applyMode = (mode: MessengerMode) => {
    if (!iframe) return;
    if (mode === "collapsed") {
      iframe.style.display = "none";
      return;
    }
    iframe.style.display = "block";
    const css = mode === "fullscreen" ? FULLSCREEN_CSS : PANEL_CSS;
    Object.assign(iframe.style, css);
  };

  const onMessage = (event: MessageEvent) => {
    if (event.origin !== opts.iframeOrigin) return;
    if (!iframe || event.source !== iframe.contentWindow) return;
    const msg = readEnvelope<IframeToHost>(event.data);
    if (!msg) return;
    switch (msg.type) {
      case "ready":
        // Reply with the resolved config, targeting the iframe origin only.
        iframe.contentWindow?.postMessage(
          envelope({ type: "init", config: opts.config }),
          opts.iframeOrigin
        );
        break;
      case "resize":
        applyMode(msg.mode);
        opts.onResize?.(msg.mode);
        break;
      case "close":
        close();
        opts.onClose?.();
        break;
      case "notify":
        opts.onNotify?.(msg.unread);
        break;
    }
  };
  window.addEventListener("message", onMessage);

  const ensureIframe = () => {
    if (iframe) return;
    iframe = document.createElement("iframe");
    iframe.title = "Chat";
    iframe.src = opts.iframeSrc;
    iframe.setAttribute(
      "sandbox",
      "allow-scripts allow-forms allow-popups allow-same-origin"
    );
    iframe.setAttribute("allow", "microphone; autoplay; clipboard-write");
    applyMode("panel");
    opts.mount.appendChild(iframe);
  };

  function open_(): void {
    ensureIframe();
    open = true;
    applyMode("panel");
    iframe?.contentWindow?.postMessage(
      envelope({ type: "visibility", open: true }),
      opts.iframeOrigin
    );
  }

  function close(): void {
    open = false;
    applyMode("collapsed");
    iframe?.contentWindow?.postMessage(
      envelope({ type: "visibility", open: false }),
      opts.iframeOrigin
    );
  }

  return {
    open: open_,
    close,
    isOpen: () => open,
    destroy() {
      window.removeEventListener("message", onMessage);
      iframe?.remove();
      iframe = null;
    },
  };
}
