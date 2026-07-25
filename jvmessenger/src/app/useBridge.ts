/**
 * React hook wrapping the iframe bridge: exposes the delivered config, the
 * current open state, and the outbound resize/close/notify actions.
 */

import { useEffect, useRef, useState } from "react";
import type { MessengerConfig } from "../shared/config";
import type { MessengerMode } from "../shared/protocol";
import { createIframeBridge, type IframeBridge } from "./bridge-iframe";

export interface BridgeApi {
  config: MessengerConfig | null;
  open: boolean;
  /** Text typed in the launcher teaser, delivered once by the host. */
  prefill: string | null;
  /** Acknowledge a consumed prefill so it isn't sent twice. */
  clearPrefill: () => void;
  resize: (mode: MessengerMode) => void;
  close: () => void;
  notify: (unread: number) => void;
}

export function useBridge(): BridgeApi {
  const [config, setConfig] = useState<MessengerConfig | null>(null);
  const [open, setOpen] = useState(true);
  const [prefill, setPrefill] = useState<string | null>(null);
  const ref = useRef<IframeBridge | null>(null);

  useEffect(() => {
    const bridge = createIframeBridge({
      onConfig: setConfig,
      onVisibility: setOpen,
      onPrefill: setPrefill,
    });
    ref.current = bridge;
    return () => bridge.destroy();
  }, []);

  return {
    config,
    open,
    prefill,
    clearPrefill: () => setPrefill(null),
    resize: (mode) => ref.current?.resize(mode),
    close: () => ref.current?.close(),
    notify: (unread) => ref.current?.notify(unread),
  };
}
