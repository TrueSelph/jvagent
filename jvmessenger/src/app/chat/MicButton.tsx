/** Mic button: records a clip, transcribes via the STT endpoint, and drops the
 * transcript into the composer. Disabled until a session token exists. */

import { useCallback, useRef, useState } from "react";
import { useComposerRuntime } from "@assistant-ui/react";
import { MicIcon, SquareIcon } from "lucide-react";
import { transcribe } from "../streaming/voiceClient";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { blobToBase64, useChatServices } from "./context";

export function MicButton() {
  const { config, getToken } = useChatServices();
  const composer = useComposerRuntime();
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const stop = useCallback(() => recorderRef.current?.stop(), []);

  const start = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return;
    }
    const recorder = new MediaRecorder(stream);
    recorderRef.current = recorder;
    chunksRef.current = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunksRef.current.push(e.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || "audio/webm",
      });
      if (!blob.size) return;
      setBusy(true);
      try {
        const b64 = await blobToBase64(blob);
        const text = await transcribe(
          config.agentUrl,
          config.agentId,
          token,
          b64,
          blob.type
        );
        if (text) {
          const existing = composer.getState().text;
          composer.setText(existing ? `${existing} ${text}` : text);
        }
      } finally {
        setBusy(false);
      }
    };
    recorder.start();
    setRecording(true);
  }, [config, getToken, composer]);

  const disabled = busy || !getToken();
  return (
    <TooltipIconButton
      tooltip={
        disabled
          ? "Send a message first to enable voice"
          : recording
            ? "Stop recording"
            : "Record voice"
      }
      onClick={recording ? stop : start}
      disabled={disabled}
      className={recording ? "text-destructive" : ""}
    >
      {recording ? <SquareIcon /> : <MicIcon />}
    </TooltipIconButton>
  );
}
