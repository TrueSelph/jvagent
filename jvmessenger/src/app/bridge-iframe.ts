/**
 * iframe side of the loader ↔ iframe bridge. Announces readiness to the host,
 * receives the resolved config + visibility changes, and sends resize/close/
 * notify requests back — always to the captured host origin.
 */

import type { MessengerConfig } from "../shared/config";
import {
  type IframeToHost,
  type MessengerMode,
  envelope,
  readEnvelope,
  type HostToIframe,
} from "../shared/protocol";

export interface IframeBridge {
  /** Ask the host to resize the iframe container. */
  resize(mode: MessengerMode): void;
  /** Ask the host to close (collapse) the messenger. */
  close(): void;
  /** Report an unread count so the host can badge the launcher. */
  notify(unread: number): void;
  /** Tear down the message listener. */
  destroy(): void;
}

/**
 * Wire up the iframe bridge. `onConfig` fires once when the host delivers the
 * `init` message; `onVisibility` fires on every open/close toggle.
 */
export function createIframeBridge(handlers: {
  onConfig: (config: MessengerConfig) => void;
  onVisibility?: (open: boolean) => void;
}): IframeBridge {
  // Standalone (not framed): a dev harness sets `__JVMESSENGER_DEV_CONFIG__` so the
  // app renders without a host. Never taken in production, where the iframe is
  // always framed by the loader.
  const standalone = window.parent === window || window.parent == null;
  const devConfig = (
    window as unknown as { __JVMESSENGER_DEV_CONFIG__?: MessengerConfig }
  ).__JVMESSENGER_DEV_CONFIG__;
  if (standalone && devConfig) {
    queueMicrotask(() => handlers.onConfig(devConfig));
    return {
      resize: () => {},
      close: () => {},
      notify: () => {},
      destroy: () => {},
    };
  }

  // Host origin is unknown until the first `init`; captured then and enforced
  // on every subsequent inbound + used as the target for every outbound.
  let hostOrigin: string | null = null;

  const post = (message: IframeToHost) => {
    // Before init we don't know the host origin; `ready` carries no data so a
    // wildcard target is acceptable for that single bootstrap message only.
    window.parent?.postMessage(envelope(message), hostOrigin ?? "*");
  };

  const onMessage = (event: MessageEvent) => {
    // Once bound to a host origin, reject anything else.
    if (hostOrigin && event.origin !== hostOrigin) return;
    if (event.source !== window.parent) return;
    const msg = readEnvelope<HostToIframe>(event.data);
    if (!msg) return;
    if (msg.type === "init") {
      hostOrigin = event.origin;
      handlers.onConfig(msg.config);
    } else if (msg.type === "visibility") {
      handlers.onVisibility?.(msg.open);
    }
  };

  window.addEventListener("message", onMessage);
  // Announce readiness so the host replies with `init`.
  post({ type: "ready" });

  return {
    resize: (mode) => post({ type: "resize", mode }),
    close: () => post({ type: "close" }),
    notify: (unread) => post({ type: "notify", unread }),
    destroy: () => window.removeEventListener("message", onMessage),
  };
}
