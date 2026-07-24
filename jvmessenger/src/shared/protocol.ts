/**
 * postMessage contract between the loader (host DOM) and the chat app (iframe).
 *
 * Both sides MUST validate `event.origin` and check `source` + `v` on every
 * inbound message. Config flows over this channel (never the URL), so the
 * handshake is the single trust boundary between the customer's page and the
 * messenger.
 *
 * Handshake sequence:
 *   1. iframe app mounts → posts `ready` to its parent.
 *   2. host receives `ready` (from the known iframe origin) → replies `init`
 *      with the resolved {@link MessengerConfig}.
 *   3. app captures the host origin from the `init` event and uses it as the
 *      target origin for every subsequent post.
 */

import type { MessengerConfig } from "./config";

/** Marker identifying our messages amid other postMessage traffic. */
export const PROTOCOL_SOURCE = "jvmessenger" as const;

/** Protocol version. Bump on breaking message-shape changes. */
export const PROTOCOL_VERSION = 1 as const;

/** Visible size mode the host applies to the iframe container. */
export type MessengerMode = "collapsed" | "panel" | "fullscreen";

/** iframe → host messages. */
export type IframeToHost =
  | { type: "ready" }
  | { type: "resize"; mode: MessengerMode; width?: number; height?: number }
  | { type: "close" }
  | { type: "notify"; unread: number };

/** host → iframe messages. */
export type HostToIframe =
  | { type: "init"; config: MessengerConfig }
  | { type: "visibility"; open: boolean };

type AnyMessage = IframeToHost | HostToIframe;

/** Envelope wrapping every message with source + version markers. */
export interface Envelope<T extends AnyMessage = AnyMessage> {
  source: typeof PROTOCOL_SOURCE;
  v: typeof PROTOCOL_VERSION;
  message: T;
}

/** Wrap a message in the versioned envelope. */
export function envelope<T extends AnyMessage>(message: T): Envelope<T> {
  return { source: PROTOCOL_SOURCE, v: PROTOCOL_VERSION, message };
}

/**
 * Validate + unwrap an inbound postMessage payload. Returns the inner message
 * only when the envelope is well-formed and version-matched; otherwise null.
 * Callers must additionally verify `event.origin` against the expected origin.
 */
export function readEnvelope<T extends AnyMessage = AnyMessage>(
  data: unknown
): T | null {
  if (!data || typeof data !== "object") return null;
  const env = data as Partial<Envelope<T>>;
  if (env.source !== PROTOCOL_SOURCE) return null;
  if (env.v !== PROTOCOL_VERSION) return null;
  if (!env.message || typeof env.message !== "object") return null;
  return env.message as T;
}
