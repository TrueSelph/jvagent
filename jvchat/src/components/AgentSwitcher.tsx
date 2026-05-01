import { useEffect, useMemo, useRef, useState } from "react";
import { useAgents } from "../hooks/useAgents";
import { useNavigate, useLocation } from "react-router-dom";
import { ChevronDown, Loader2 } from "lucide-react";
import { cn } from "../lib/utils";
import type { Agent } from "../types/agent";
import { saveSelectedAgent } from "../utils/storage";

function displayName(a: Agent): string {
  return a.alias || a.name || a.id || "Agent";
}

function AgentRow({
  agent,
  selected,
  onPick,
}: {
  agent: Agent;
  selected: boolean;
  onPick: () => void;
}) {
  const title = displayName(agent);
  return (
    <button
      type="button"
      data-close-agent-switch="1"
      onClick={onPick}
      className={cn(
        "flex w-full gap-3 rounded-xl px-3 py-3 text-left transition-colors",
        selected
          ? "bg-zinc-100 dark:bg-zinc-800"
          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/60",
      )}
    >
      <div className="flex-shrink-0">
        {agent.avatar_url ? (
          <img
            src={agent.avatar_url}
            alt=""
            className="h-12 w-12 rounded-full border border-zinc-200 object-cover dark:border-white/10"
          />
        ) : (
          <div className="flex h-12 w-12 items-center justify-center rounded-full border border-zinc-200 bg-zinc-100 text-xs font-semibold uppercase text-zinc-500 dark:border-white/10 dark:bg-zinc-800 dark:text-zinc-300">
            {title.slice(0, 2)}
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-semibold leading-tight text-zinc-900 dark:text-zinc-50">{title}</div>
        {agent.description ? (
          <p className="mt-1 line-clamp-2 text-xs leading-snug text-zinc-500 dark:text-zinc-400">
            {agent.description}
          </p>
        ) : (
          <div className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
            Agent
          </div>
        )}
      </div>
    </button>
  );
}

type AgentSwitcherProps = {
  /** Compact trigger width (header bar style); wide trigger fills horizontal space */
  variant?: "inline" | "full";
};

/**
 * Tschat-style agent switcher: wide trigger plus rich selectable list (Radix-free).
 */
export function AgentSwitcher({ variant = "inline" }: AgentSwitcherProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const match = /^\/chat\/([^/]+)\/?$/.exec(location.pathname);
  const agentIdFromUrl = match?.[1];
  let decodedId = "";
  try {
    decodedId = agentIdFromUrl ? decodeURIComponent(agentIdFromUrl) : "";
  } catch {
    decodedId = agentIdFromUrl ?? "";
  }
  const { agents, loading } = useAgents();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const current = useMemo(
    () => agents.find((a) => a.id === decodedId),
    [agents, decodedId],
  );

  const switchTo = (agent: Agent) => {
    if (!agent.id) return;
    saveSelectedAgent(agent.name || agent.id);
    navigate(`/chat/${encodeURIComponent(agent.id)}`);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const triggerLabel = loading
    ? "Loading agents…"
    : current
      ? displayName(current)
      : decodedId || agentIdFromUrl
        ? "Unknown agent"
        : agents.length === 0
          ? "No agents"
          : "Select agent";

  const showChevron = !loading && agents.length > 0 && Boolean(agentIdFromUrl);

  const isFull = variant === "full";

  return (
    <div
      ref={rootRef}
      className={cn(
        "relative min-w-0",
        isFull ? "w-full" : "inline-block max-w-[min(21rem,calc(100vw-8rem))] align-middle my-2.5 sm:max-w-[min(22rem,calc(100vw-12rem))]",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={loading || agents.length === 0}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex min-h-[3.25rem] items-center gap-3 rounded-xl border px-4 py-2 text-left outline-none transition-colors disabled:opacity-70",
          isFull ? "w-full max-w-full" : "w-full min-w-[12rem]",
          "border-zinc-200 bg-zinc-50 hover:bg-zinc-100 dark:border-white/10 dark:bg-zinc-800/70 dark:hover:bg-zinc-800",
          open &&
            "ring-2 ring-zinc-400/25 dark:ring-white/15",
        )}
      >
        {current?.avatar_url ? (
          <img
            src={current.avatar_url}
            alt=""
            className="h-8 w-8 flex-shrink-0 rounded-full border border-white/10 object-cover"
          />
        ) : (
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-zinc-200 text-[10px] font-bold uppercase text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300">
            {(current ? displayName(current) : "?").slice(0, 2)}
          </div>
        )}
        <div className="min-w-0 flex-1 overflow-hidden">
          <div className="truncate font-semibold text-zinc-900 dark:text-zinc-50">
            {triggerLabel}
          </div>
          {current?.description ? (
            <p className="line-clamp-1 text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">
              {current.description}
            </p>
          ) : (
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
              Agent
            </div>
          )}
        </div>
        {loading ? (
          <Loader2 className="h-5 w-5 flex-shrink-0 animate-spin text-zinc-400" aria-hidden />
        ) : showChevron ? (
          <ChevronDown
            className={cn(
              "h-5 w-5 flex-shrink-0 text-zinc-400 transition-transform duration-150",
              open && "rotate-180",
            )}
            aria-hidden
            strokeWidth={2}
          />
        ) : null}
      </button>

      {open ? (
        <div
          className={cn(
            "absolute top-[calc(100%+8px)] z-[100] max-h-[min(28rem,calc(100vh-10rem))] overflow-y-auto rounded-xl border border-zinc-200 bg-white p-2 shadow-xl dark:border-white/10 dark:bg-zinc-900",
            isFull
              ? "left-0 right-0 sm:left-1/2 sm:right-auto sm:w-[min(340px,calc(100vw-3rem))] sm:-translate-x-1/2"
              : "left-0 w-[min(340px,calc(100vw-2rem))] max-[480px]:w-[calc(100vw-2rem)]",
          )}
          role="listbox"
          aria-label="Agents"
        >
          <div className="px-2 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-zinc-500">
            Switch agent
          </div>
          {agents.map((a) => (
            <AgentRow
              key={a.id}
              agent={a}
              selected={a.id === current?.id}
              onPick={() => switchTo(a)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
