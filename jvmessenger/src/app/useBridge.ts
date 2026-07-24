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
  resize: (mode: MessengerMode) => void;
  close: () => void;
  notify: (unread: number) => void;
}

export function useBridge(): BridgeApi {
  const [config, setConfig] = useState<MessengerConfig | null>(null);
  const [open, setOpen] = useState(true);
  const ref = useRef<IframeBridge | null>(null);

  useEffect(() => {
    const bridge = createIframeBridge({
      onConfig: setConfig,
      onVisibility: setOpen,
    });
    ref.current = bridge;
    return () => bridge.destroy();
  }, []);

  return {
    config,
    open,
    resize: (mode) => ref.current?.resize(mode),
    close: () => ref.current?.close(),
    notify: (unread) => ref.current?.notify(unread),
  };
}
