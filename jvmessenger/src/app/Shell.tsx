/**
 * Popup shell chrome: a shadcn-styled header (avatar + name/description, an
 * overflow ⋯ menu holding the expand/collapse control, and close) wrapping the
 * chat body. In fullscreen the content is centered in a max-width card over a
 * blurred backdrop rather than stretching edge-to-edge.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  DownloadIcon,
  Maximize2Icon,
  Minimize2Icon,
  MoonIcon,
  MoreHorizontalIcon,
  SunIcon,
  XIcon,
} from "lucide-react";
import type { MessengerConfig } from "../shared/config";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import type { BridgeApi } from "./useBridge";

function MenuItem({
  icon,
  label,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="hover:bg-accent hover:text-accent-foreground flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm"
    >
      {icon}
      {label}
    </button>
  );
}

function OverflowMenu({
  showFullscreen,
  fullscreen,
  onToggleFullscreen,
  theme,
  onToggleTheme,
  onDownloadTranscript,
}: {
  showFullscreen: boolean;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onDownloadTranscript: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pick = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <div className="relative" ref={ref}>
      <TooltipIconButton tooltip="Menu" onClick={() => setOpen((o) => !o)}>
        <MoreHorizontalIcon />
      </TooltipIconButton>
      {open && (
        <div className="border-border bg-popover text-popover-foreground animate-in fade-in zoom-in-95 absolute right-0 z-50 mt-1 min-w-48 overflow-hidden rounded-lg border p-1 shadow-md">
          {showFullscreen && (
            <MenuItem
              icon={
                fullscreen ? (
                  <Minimize2Icon className="size-4" />
                ) : (
                  <Maximize2Icon className="size-4" />
                )
              }
              label={fullscreen ? "Exit fullscreen" : "Expand window"}
              onSelect={pick(onToggleFullscreen)}
            />
          )}
          <MenuItem
            icon={
              theme === "dark" ? (
                <SunIcon className="size-4" />
              ) : (
                <MoonIcon className="size-4" />
              )
            }
            label={theme === "dark" ? "Light theme" : "Dark theme"}
            onSelect={pick(onToggleTheme)}
          />
          <MenuItem
            icon={<DownloadIcon className="size-4" />}
            label="Download transcript"
            onSelect={pick(onDownloadTranscript)}
          />
        </div>
      )}
    </div>
  );
}

export function Shell({
  config,
  bridge,
  theme,
  onToggleTheme,
  onDownloadTranscript,
  children,
}: {
  config: MessengerConfig;
  bridge: BridgeApi;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onDownloadTranscript: () => void;
  children: ReactNode;
}) {
  const [fullscreen, setFullscreen] = useState(false);

  // Exit fullscreen whenever the panel is hidden, so reopening always returns to
  // the normal panel layout. Otherwise the app stays in its fullscreen layout
  // (centered card + backdrop) while the host resets the iframe to panel size on
  // reopen — leaving the popup oddly "wrapped in a container".
  useEffect(() => {
    if (!bridge.open) setFullscreen(false);
  }, [bridge.open]);

  const toggleFullscreen = () => {
    const next = !fullscreen;
    setFullscreen(next);
    bridge.resize(next ? "fullscreen" : "panel");
  };

  const card = (
    <div className="bg-background flex h-full min-h-0 flex-col overflow-hidden rounded-[inherit]">
      <header className="border-border flex flex-none items-center justify-between gap-2 border-b px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <img
            className="border-border size-10 flex-none rounded-full border object-cover"
            src={config.avatar}
            alt=""
          />
          <div className="flex min-w-0 flex-col justify-center">
            <span className="text-foreground truncate text-sm leading-tight font-semibold">
              {config.title}
            </span>
            {config.description && (
              <span className="text-muted-foreground line-clamp-2 text-xs leading-snug">
                {config.description}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-none items-center gap-0.5">
          <OverflowMenu
            showFullscreen={config.fullscreen}
            fullscreen={fullscreen}
            onToggleFullscreen={toggleFullscreen}
            theme={theme}
            onToggleTheme={onToggleTheme}
            onDownloadTranscript={onDownloadTranscript}
          />
          <TooltipIconButton tooltip="Close" onClick={() => bridge.close()}>
            <XIcon />
          </TooltipIconButton>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </div>
  );

  if (fullscreen) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-black/25 p-4 backdrop-blur-md">
        <div className="h-full max-h-[900px] w-full max-w-[720px] overflow-hidden rounded-2xl shadow-xl">
          {card}
        </div>
      </div>
    );
  }

  return <div className="h-full">{card}</div>;
}
