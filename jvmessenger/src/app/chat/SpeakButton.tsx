/** Per-assistant-message "read aloud" button (TTS endpoint). While audio is
 * playing the button becomes a Stop control so playback can be cut short. */

import { useCallback, useRef, useState } from "react";
import { useMessage } from "@assistant-ui/react";
import { Loader2Icon, SquareIcon, Volume2Icon } from "lucide-react";
import { synthesize } from "../streaming/voiceClient";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { useChatServices } from "./context";

export function SpeakButton() {
  const { config, getToken } = useChatServices();
  const message = useMessage();
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const text = message.content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join(" ")
    .trim();

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setPlaying(false);
  }, []);

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
      audio.onended = () => setPlaying(false);
      audio.onpause = () => setPlaying(false);
      try {
        await audio.play();
        setPlaying(true);
      } catch {
        setPlaying(false);
      }
    } finally {
      setBusy(false);
    }
  }, [config, getToken, text, busy]);

  if (!text || !getToken()) return null;
  return (
    <TooltipIconButton
      tooltip={busy ? "Loading…" : playing ? "Stop" : "Read aloud"}
      onClick={playing ? stop : speak}
      disabled={busy}
      className={playing ? "text-destructive" : ""}
    >
      {busy ? (
        <Loader2Icon className="animate-spin" />
      ) : playing ? (
        <SquareIcon />
      ) : (
        <Volume2Icon />
      )}
    </TooltipIconButton>
  );
}
