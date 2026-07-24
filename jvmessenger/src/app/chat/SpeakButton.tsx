/** Per-assistant-message "read aloud" button (TTS endpoint). */

import { useCallback, useRef, useState } from "react";
import { useMessage } from "@assistant-ui/react";
import { Volume2Icon, Loader2Icon } from "lucide-react";
import { synthesize } from "../streaming/voiceClient";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { useChatServices } from "./context";

export function SpeakButton() {
  const { config, getToken } = useChatServices();
  const message = useMessage();
  const [busy, setBusy] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const text = message.content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join(" ")
    .trim();

  const speak = useCallback(async () => {
    const token = getToken();
    if (!token || !text || busy) return;
    setBusy(true);
    try {
      const out = await synthesize(config.agentUrl, config.agentId, token, text);
      if (!out) return;
      const audio = new Audio(`data:${out.mimeType};base64,${out.audioBase64}`);
      audioRef.current?.pause();
      audioRef.current = audio;
      await audio.play().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }, [config, getToken, text, busy]);

  if (!text || !getToken()) return null;
  return (
    <TooltipIconButton tooltip="Read aloud" onClick={speak} disabled={busy}>
      {busy ? <Loader2Icon className="animate-spin" /> : <Volume2Icon />}
    </TooltipIconButton>
  );
}
