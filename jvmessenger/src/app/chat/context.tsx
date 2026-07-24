/** Shares config + session-token access with components rendered deep inside
 * the assistant-ui message tree (e.g. the per-message Speak button). */

import { createContext, useContext, type ReactNode } from "react";
import type { MessengerConfig } from "../../shared/config";

export interface ChatServices {
  config: MessengerConfig;
  getToken: () => string | undefined;
}

const Ctx = createContext<ChatServices | null>(null);

export function ChatServicesProvider({
  value,
  children,
}: {
  value: ChatServices;
  children: ReactNode;
}) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useChatServices(): ChatServices {
  const v = useContext(Ctx);
  if (!v) throw new Error("useChatServices outside provider");
  return v;
}

/** Convert a Blob to a bare base64 string (no data: prefix). */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onloadend = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(blob);
  });
}
