import { useEffect, useRef, useState } from "react";
import {
  Settings,
  BookMarked,
  BookOpenCheck,
  SlidersHorizontal,
  Database,
  Network,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/utils";

export interface ComposerToolsMenuProps {
  disabled?: boolean;
  hasDocuments: boolean;
  onDocuments: () => void;
  onInteractionDebug: () => void;
  onActionConfig: () => void;
  onLongMemory: () => void;
  onAppGraph: () => void;
}

function menuModifierPrefix(): string {
  if (typeof navigator === "undefined") return "Ctrl";
  return /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent) ? "⌘" : "Ctrl";
}

function MenuButton({
  icon: Icon,
  label,
  shortcut,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  shortcut?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm",
        "text-zinc-800 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-800",
      )}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <Icon className="h-4 w-4 flex-shrink-0 text-zinc-500 dark:text-zinc-400" aria-hidden strokeWidth={1.75} />
      <span className="min-w-0 flex-1 font-medium">{label}</span>
      {shortcut ? (
        <span className="flex-shrink-0 text-xs font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
          {shortcut}
        </span>
      ) : null}
    </button>
  );
}

/** Expandable tools menu anchored by the composer attach control. */
export function ComposerToolsMenu({
  disabled = false,
  hasDocuments,
  onDocuments,
  onInteractionDebug,
  onActionConfig,
  onLongMemory,
  onAppGraph,
}: ComposerToolsMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const mod = menuModifierPrefix();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const wrapAction = (fn: () => void) => () => {
    fn();
    setOpen(false);
  };

  return (
    <div ref={wrapRef} className="relative flex shrink-0">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={disabled}
        aria-label="Composer tools"
        title={
          hasDocuments
            ? `Composer tools — ${mod}D Documents, ${mod}I Interaction debug, ${mod}A Action config, ${mod}L Long memory, ${mod}G App graph`
            : `Composer tools — ${mod}I Interaction debug, ${mod}A Action config, ${mod}L Long memory, ${mod}G App graph`
        }
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex size-[34px] items-center justify-center rounded-full border border-transparent text-xs font-semibold text-zinc-500 transition-colors disabled:opacity-50",
          "hover:border-zinc-200 hover:bg-zinc-100 hover:text-zinc-700 dark:border-transparent dark:text-zinc-400 dark:hover:border-white/10 dark:hover:bg-zinc-800 dark:hover:text-zinc-200",
          open &&
            "border-zinc-200 bg-zinc-100 dark:border-white/10 dark:bg-zinc-800",
        )}
      >
        <Settings className="size-[1.125rem]" strokeWidth={1.75} aria-hidden />
      </button>

      {open ? (
        <div
          className={cn(
            "absolute bottom-[calc(100%+6px)] left-0 z-50 flex min-w-[15.5rem] flex-col rounded-xl border border-zinc-200 bg-white p-1.5 shadow-lg",
            "dark:border-white/10 dark:bg-zinc-900 dark:shadow-2xl",
          )}
          role="menu"
        >
          {hasDocuments ? (
            <MenuButton
              icon={BookOpenCheck}
              label="Documents"
              shortcut={`${mod}D`}
              onClick={wrapAction(onDocuments)}
            />
          ) : null}
          <MenuButton
            icon={BookMarked}
            label="Interaction debug"
            shortcut={`${mod}I`}
            onClick={wrapAction(onInteractionDebug)}
          />
          <MenuButton
            icon={SlidersHorizontal}
            label="Action config"
            shortcut={`${mod}A`}
            onClick={wrapAction(onActionConfig)}
          />
          <MenuButton
            icon={Database}
            label="Long memory"
            shortcut={`${mod}L`}
            onClick={wrapAction(onLongMemory)}
          />
          <MenuButton
            icon={Network}
            label="App graph"
            shortcut={`${mod}G`}
            onClick={wrapAction(onAppGraph)}
          />
        </div>
      ) : null}
    </div>
  );
}
