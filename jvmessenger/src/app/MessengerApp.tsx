/**
 * Root of the iframe chat app. Waits for the host handshake, applies the theme,
 * mounts the assistant-ui runtime, and renders the popup shell around the
 * assistant-ui base Thread.
 */

import { useEffect, useMemo, useState } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import type { MessengerConfig } from "../shared/config";
import { useBridge } from "./useBridge";
import { Shell } from "./Shell";
import { useChatRuntime } from "./chat/useChatRuntime";
import { Thread } from "./chat/Thread";
import { DEFAULT_AVATAR, fetchAgentProfile } from "./avatar";

interface ResolvedProfile {
  avatar: string;
  title: string;
  description?: string;
}

function useResolvedProfile(config: MessengerConfig): ResolvedProfile {
  // Explicit data-* wins; otherwise fall back to the agent's relayed profile,
  // then a built-in default (avatar only).
  const [profile, setProfile] = useState<ResolvedProfile>({
    avatar: config.avatar || DEFAULT_AVATAR,
    title: config.title,
    description: config.description,
  });
  useEffect(() => {
    let alive = true;
    // Only hit the network for fields the embed didn't pin.
    const needFetch =
      !config.avatar || config.title === "Chat" || !config.description;
    if (!needFetch) {
      setProfile({
        avatar: config.avatar || DEFAULT_AVATAR,
        title: config.title,
        description: config.description,
      });
      return;
    }
    fetchAgentProfile(config.agentUrl, config.agentId).then((p) => {
      if (!alive) return;
      setProfile({
        avatar: config.avatar || p.avatar || DEFAULT_AVATAR,
        title: config.title !== "Chat" ? config.title : p.name || config.title,
        description: config.description || p.description || undefined,
      });
    });
    return () => {
      alive = false;
    };
  }, [
    config.avatar,
    config.title,
    config.description,
    config.agentUrl,
    config.agentId,
  ]);
  return profile;
}

function useSystemDark(): boolean {
  const [dark, setDark] = useState<boolean>(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const handler = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener?.("change", handler);
    return () => mq.removeEventListener?.("change", handler);
  }, []);
  return dark;
}

export function MessengerApp() {
  const bridge = useBridge();
  const { config } = bridge;
  const systemDark = useSystemDark();
  // A manual override from the header menu wins over the embed/system default.
  const [override, setOverride] = useState<"light" | "dark" | null>(null);

  // `auto` follows the live system setting; explicit light/dark pins it; a
  // user override (from the ⋯ menu) beats both.
  const theme = useMemo<"light" | "dark">(() => {
    if (override) return override;
    const pref = config?.theme ?? "auto";
    if (pref === "auto") return systemDark ? "dark" : "light";
    return pref;
  }, [config, systemDark, override]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = () =>
    setOverride(theme === "dark" ? "light" : "dark");

  if (!config) {
    return (
      <div
        className="text-muted-foreground bg-background flex h-full items-center justify-center text-sm"
        role="status"
      >
        Connecting…
      </div>
    );
  }

  return (
    <ChatSurface
      config={config}
      bridge={bridge}
      theme={theme}
      onToggleTheme={toggleTheme}
    />
  );
}

function ChatSurface({
  config,
  bridge,
  theme,
  onToggleTheme,
}: {
  config: MessengerConfig;
  bridge: ReturnType<typeof useBridge>;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  const {
    runtime,
    sendText,
    getToken,
    attachments,
    addAttachment,
    removeAttachment,
    suggestions,
    downloadTranscript,
  } = useChatRuntime(config);
  const profile = useResolvedProfile(config);
  const shellConfig = useMemo(
    () => ({
      ...config,
      avatar: profile.avatar,
      title: profile.title,
      description: profile.description,
    }),
    [config, profile]
  );
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Shell
        config={shellConfig}
        bridge={bridge}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onDownloadTranscript={downloadTranscript}
      >
        <Thread
          config={shellConfig}
          sendText={sendText}
          getToken={getToken}
          attachments={attachments}
          addAttachment={addAttachment}
          removeAttachment={removeAttachment}
          suggestions={suggestions}
        />
      </Shell>
    </AssistantRuntimeProvider>
  );
}
