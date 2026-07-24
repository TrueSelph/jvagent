/** Mic button. Prefers real-time transcription (streams mic audio to the live
 * STT WebSocket and drops interim + final text into the composer as you speak);
 * falls back to record-then-transcribe (batch) when streaming is unavailable or
 * fails to start. Disabled until a session token exists. */

import { useCallback, useRef, useState } from "react";
import { useComposerRuntime } from "@assistant-ui/react";
import { Loader2Icon, MicIcon, SquareIcon } from "lucide-react";
import { transcribe } from "../streaming/voiceClient";
import {
  startLiveTranscription,
  type LiveTranscriptionController,
} from "../streaming/voiceStreamClient";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { blobToBase64, useChatServices } from "./context";

export function MicButton() {
  const { config, getToken } = useChatServices();
  const composer = useComposerRuntime();
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);

  // Live-mode state: composer text present before dictation (prefix), the
  // committed final segments, and the current interim hypothesis.
  const liveRef = useRef<LiveTranscriptionController | null>(null);
  const prefixRef = useRef("");
  const committedRef = useRef("");

  // Batch-fallback recorder state.
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const renderLive = useCallback(
    (interim: string) => {
      composer.setText(
        (prefixRef.current + committedRef.current + interim).trimStart()
      );
    },
    [composer]
  );

  const startBatch = useCallback(
    async (token: string) => {
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
    },
    [config, composer]
  );

  const start = useCallback(async () => {
    const token = getToken();
    if (!token) return;

    const existing = composer.getState().text;
    prefixRef.current = existing ? existing + " " : "";
    committedRef.current = "";

    // Try real-time streaming first.
    const controller = await startLiveTranscription(
      config.agentUrl,
      config.agentId,
      token,
      {
        onInterim: (t) => renderLive(t),
        onFinal: (t) => {
          committedRef.current += t.trim() + " ";
          renderLive("");
        },
        onReady: () => setRecording(true),
        onError: () => {
          liveRef.current = null;
          setRecording(false);
        },
      }
    );

    if (controller) {
      liveRef.current = controller;
      setRecording(true);
      return;
    }
    // Fall back to record-then-transcribe.
    await startBatch(token);
  }, [config, getToken, composer, renderLive, startBatch]);

  const stop = useCallback(() => {
    if (liveRef.current) {
      liveRef.current.stop();
      liveRef.current = null;
      // Keep the committed transcript; drop the trailing space.
      composer.setText((prefixRef.current + committedRef.current).trimEnd());
      setRecording(false);
      return;
    }
    recorderRef.current?.stop();
  }, [composer]);

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
      {busy ? (
        <Loader2Icon className="animate-spin" />
      ) : recording ? (
        <SquareIcon />
      ) : (
        <MicIcon />
      )}
    </TooltipIconButton>
  );
}
