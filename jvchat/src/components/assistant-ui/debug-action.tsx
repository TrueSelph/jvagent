"use client";

import { createContext, useContext, type FC } from "react";
import { BugIcon } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import type { JvAssistantMeta } from "@/lib/threadMessages";
import type { Message } from "@/types/message";

/** jvchat bridge: lets the per-message Debug action open the legacy dialog. */
export const DebugContext = createContext<{
  openDebug: (m: Message | null) => void;
} | null>(null);

/**
 * jvchat-specific addition to assistant-ui's action bar: a "Debug" button that
 * opens the legacy debug dialog from the final-chunk debugData carried on the
 * message metadata. Hidden when the message has no debug payload yet.
 */
export const MessageDebugAction: FC = () => {
  const ctx = useContext(DebugContext);
  const debugMessage = useAuiState(
    (s) =>
      (s.message.metadata?.custom as unknown as JvAssistantMeta | undefined)
        ?.debugMessage ?? null,
  );
  if (!ctx || !debugMessage) return null;
  return (
    <TooltipIconButton
      tooltip="Debug"
      onClick={() => ctx.openDebug(debugMessage)}
    >
      <BugIcon />
    </TooltipIconButton>
  );
};
