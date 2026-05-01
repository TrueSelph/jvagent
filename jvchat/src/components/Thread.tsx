import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types/message";
import React from "react";
import {
  ArrowDown,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Pencil,
} from "lucide-react";
import type { SendMessageOptions } from "../hooks/useStreaming";
import { cn } from "../lib/utils";
import { useTheme } from "../context/ThemeContext";
import { tryParseJsonDisplay } from "../utils/tryParseJsonDisplay";
import { resolveMediaUrl } from "../utils/mediaUrl";
import { JsonViewer } from "./JsonViewer";
import { MessageInput } from "./MessageInput";
import { WelcomeScreen } from "./WelcomeScreen";

function metaString(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return String(v);
  return "";
}

function contentAlreadyEmbedsMediaUrl(
  content: string,
  rawUrl: string,
  resolvedSrc: string,
): boolean {
  const c = content || "";
  if (!c.trim()) return false;
  if (rawUrl && c.includes(rawUrl)) return true;
  if (resolvedSrc && resolvedSrc !== rawUrl && c.includes(resolvedSrc))
    return true;
  return false;
}

function UserOutboundAttachments({
  messageId,
  attachments,
}: {
  messageId: string;
  attachments: NonNullable<Message["attachments"]>;
}) {
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  useEffect(() => {
    return () => {
      for (const a of attachmentsRef.current) {
        if (a.previewUrl?.startsWith("blob:")) {
          URL.revokeObjectURL(a.previewUrl);
        }
      }
    };
  }, [messageId]);

  return (
    <div className="flex flex-col gap-3 mb-2 w-full max-w-full items-end">
      {attachments.map((a, idx) => {
        const imageSrc =
          a.kind === "image"
            ? a.persistedDataUrl || a.previewUrl
            : undefined;

        return (
        <div
          key={`${messageId}-${idx}-${a.name}`}
          className="flex flex-col gap-1.5 items-end max-w-full"
        >
          {a.kind === "image" && imageSrc ? (
            <img
              src={imageSrc}
              alt=""
              className="max-h-64 max-w-[min(100%,20rem)] w-auto rounded-2xl object-contain border border-white/10 bg-black/20"
            />
          ) : a.kind === "image" ? (
            <span className="text-xs italic text-zinc-400 dark:text-zinc-500 px-2 py-1 rounded-lg border border-zinc-200 dark:border-white/10">
              Large image — preview only for this tab session
            </span>
          ) : (
            <span className="inline-flex items-center gap-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-100 dark:bg-zinc-800 px-2 py-1.5 text-xs text-zinc-700 dark:text-zinc-300">
              <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded bg-zinc-200 dark:bg-zinc-700 text-[10px] font-medium uppercase">
                file
              </span>
              <span className="truncate max-w-[12rem] text-left" title={a.name}>
                {a.name}
              </span>
            </span>
          )}
          {a.kind === "image" && imageSrc ? (
            <span
              className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate max-w-full"
              title={a.name}
            >
              {a.name}
            </span>
          ) : null}
        </div>
        );
      })}
    </div>
  );
}

function MetadataImage({
  src,
}: {
  src: string;
  role: Message["role"];
}) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  if (failed) {
    return (
      <p className="text-sm my-2 italic text-zinc-400 dark:text-zinc-500">
        Image is not available.
      </p>
    );
  }

  return (
    <img
      src={src}
      alt=""
      className="max-w-full h-auto rounded-lg my-2 block"
      onError={() => setFailed(true)}
    />
  );
}

function MessageMediaBlock({ message }: { message: Message }) {
  const meta = message.metadata;
  if (!meta) return null;
  const rawUrl = metaString(meta.media_url);
  if (!rawUrl) return null;
  const src = resolveMediaUrl(rawUrl);
  const type = metaString(meta.media_type).toLowerCase();

  if (type === "video") {
    return (
      <video
        controls
        className="max-w-full rounded-lg my-2 block"
        src={src}
      />
    );
  }
  if (type === "audio" || type === "voice") {
    return <audio controls className="w-full my-2" src={src} />;
  }
  if (type === "document" || type === "file" || type === "docs") {
    const label =
      metaString(meta.filename) || rawUrl.split("/").pop() || "Attachment";
    return (
      <a
        href={src}
        target="_blank"
        rel="noopener noreferrer"
        className="underline block my-2 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200"
      >
        {label}
      </a>
    );
  }
  if (contentAlreadyEmbedsMediaUrl(message.content || "", rawUrl, src)) {
    return null;
  }
  return <MetadataImage src={src} role={message.role} />;
}

const BrainIcon = () => (
  <svg className="size-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
  </svg>
);

const ChevronIcon = ({ open }: { open: boolean }) => (
  <svg
    className={`mt-0.5 size-4 shrink-0 transition-transform duration-200 ease-out ${open ? "rotate-0" : "-rotate-90"}`}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    aria-hidden
  >
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 9l6 6 6-6" />
  </svg>
);

export interface ThreadProps {
  messages: Message[];
  showThinking?: boolean;
  isStreaming?: boolean;
  onEditMessage?: (messageId: string, newContent: string) => void | Promise<void>;
  branchSnapshots?: Record<string, Message[][]>;
  branchVersionIndex?: Record<string, number>;
  onBranchVersionChange?: (rootId: string, index: number) => void;
  onSend: (message: string, options?: SendMessageOptions) => void;
  placeholder?: string;
  composerDisabled?: boolean;
  welcomeAgentName?: string;
  welcomeAgentAvatar?: string;
  welcomeDescription?: string;
  /** Tools menu beside send (documents, debug, …) */
  composerMenu?: React.ReactNode;
  /** Invoked when the user clicks the stop button while the composer is disabled. */
  onStop?: () => void;
}

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };
  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-2 text-zinc-500 transition-colors duration-150 hover:bg-zinc-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
      aria-label={copied ? "Copied" : "Copy message"}
    >
      {copied ? (
        <Check className="size-4 stroke-[1.5px]" aria-hidden strokeWidth={1.5} />
      ) : (
        <Copy className="size-4 stroke-[1.5px]" aria-hidden strokeWidth={1.5} />
      )}
      {copied ? (
        <span className="sr-only" role="status">
          Copied
        </span>
      ) : null}
    </button>
  );
}

function userBranchRootId(m: Message): string {
  return m.branchRootId ?? m.id;
}

interface ReasoningPanelProps {
  panelKey: string;
  interactionId: string;
  thoughts: Message[];
  open: boolean;
  onToggle: (panelKey: string, next: boolean) => void;
  variant?: "anchored" | "orphan";
}

function thoughtDisplayParagraphs(content: string | undefined): string[] {
  const raw = content ?? "";
  if (!raw) return [""];
  return raw.split(/\n{2,}/).map((p) => p.trimEnd());
}

function ReasoningPanel({
  panelKey,
  interactionId,
  thoughts,
  open,
  onToggle,
  variant = "anchored",
}: ReasoningPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const isStreaming = thoughts.some((t) => t.streaming);

  const thoughtsScrollSignature = thoughts
    .map((t) => `${t.id}:${t.content?.length ?? 0}:${Boolean(t.streaming)}`)
    .join("|");
  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (!el) return;
    const scrollToBottom = () => {
      el.scrollTop = el.scrollHeight;
    };
    requestAnimationFrame(() => requestAnimationFrame(scrollToBottom));
  }, [open, thoughtsScrollSignature, isStreaming]);

  return (
    <div
      className="mb-4 w-full"
      data-reasoning-panel={variant}
      data-interaction-id={interactionId}
    >
      <button
        type="button"
        onClick={() => onToggle(panelKey, !open)}
        className="flex items-center gap-2 py-1 text-muted-foreground text-sm transition-colors hover:text-foreground"
      >
        <BrainIcon />
        <span className="relative inline-block leading-none">
          <span>Reasoning</span>
          {isStreaming ? (
            <span
              aria-hidden
              className="shimmer pointer-events-none absolute inset-0"
            >
              Reasoning
            </span>
          ) : null}
        </span>
        <ChevronIcon open={open} />
      </button>
      {open && (
        <div className="relative overflow-hidden text-muted-foreground text-sm">
          <div
            ref={scrollRef}
            className="relative z-0 max-h-64 space-y-4 overflow-y-auto pt-2 pb-4 pl-6 leading-relaxed"
          >
            {thoughts.map((thought) => {
              const prefix =
                thought.thoughtType === "tool_call"
                  ? ">"
                  : thought.thoughtType === "tool_result"
                    ? "←"
                    : "·";
              const paragraphs = thoughtDisplayParagraphs(thought.content);
              return (
                <div key={thought.id} className="space-y-2">
                  {paragraphs.map((para, pi) => (
                    <div
                      key={`${thought.id}-p-${pi}`}
                      className="text-xs font-mono leading-[1.65] text-zinc-500 dark:text-zinc-400 pl-2 whitespace-pre-wrap break-words"
                    >
                      {pi === 0 ? (
                        <>
                          <span className="select-none opacity-90">{prefix}</span>{" "}
                          {para}
                        </>
                      ) : (
                        para
                      )}
                      {thought.streaming && pi === paragraphs.length - 1 ? (
                        <span
                          aria-hidden
                          className="aui-streaming-dot-pulse align-middle text-base leading-none text-zinc-900 dark:text-zinc-50"
                        >
                          &#9679;
                        </span>
                      ) : null}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-8 bg-[linear-gradient(to_top,var(--background),transparent)]" />
        </div>
      )}
    </div>
  );
}

export function Thread({
  messages,
  showThinking = false,
  isStreaming = false,
  onEditMessage,
  branchSnapshots = {},
  branchVersionIndex = {},
  onBranchVersionChange,
  onSend,
  placeholder = "Send a message...",
  composerDisabled = false,
  welcomeAgentName = "Agent",
  welcomeAgentAvatar,
  welcomeDescription,
  composerMenu,
  onStop,
}: ThreadProps) {
  const { theme } = useTheme();
  const jsonPanelDark = theme === "dark";

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevLenRef = useRef<number>(0);
  const [debugMessage, setDebugMessage] = useState<Message | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const debugModalPayload = useMemo(() => {
    if (!debugMessage) return { parsed: null as unknown | null, raw: "" };
    const raw =
      debugMessage.debugData?.interaction?.response ??
      debugMessage.content ??
      "";
    const s = typeof raw === "string" ? raw : String(raw);
    return { parsed: tryParseJsonDisplay(s), raw: s };
  }, [debugMessage]);

  const displayMessages = useMemo<Message[]>(() => {
    return [...messages].sort((a, b) => {
      const leftTime = Date.parse(a.timestamp || "");
      const rightTime = Date.parse(b.timestamp || "");
      const leftValid = Number.isFinite(leftTime);
      const rightValid = Number.isFinite(rightTime);

      if (leftValid && rightValid && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      const leftOrder =
        typeof a.order === "number" && Number.isFinite(a.order)
          ? a.order
          : Number.MAX_SAFE_INTEGER;
      const rightOrder =
        typeof b.order === "number" && Number.isFinite(b.order)
          ? b.order
          : Number.MAX_SAFE_INTEGER;
      if (leftOrder !== rightOrder) {
        return leftOrder - rightOrder;
      }
      return a.id.localeCompare(b.id);
    });
  }, [messages]);

  interface TurnGroup {
    key: string;
    anchorId: string | null;
    thoughts: Message[];
  }
  const turnGroups = useMemo<TurnGroup[]>(() => {
    const groups: TurnGroup[] = [];
    let pending: Message[] = [];
    let lastUserId: string | null = null;
    let orphanCounter = 0;
    for (const m of displayMessages) {
      if (m.category === "thought") {
        pending.push(m);
        continue;
      }
      if (m.role === "user") {
        if (pending.length > 0) {
          groups.push({
            key: `orphan-${lastUserId ?? `idx-${orphanCounter++}`}`,
            anchorId: null,
            thoughts: pending,
          });
          pending = [];
        }
        lastUserId = m.id;
        continue;
      }
      groups.push({
        key: `anchor-${m.id}`,
        anchorId: m.id,
        thoughts: pending,
      });
      pending = [];
    }
    if (pending.length > 0) {
      groups.push({
        key: `orphan-${lastUserId ?? `tail-${orphanCounter++}`}`,
        anchorId: null,
        thoughts: pending,
      });
    }
    return groups;
  }, [displayMessages]);

  const thoughtsByAnchorId = useMemo(() => {
    const m = new Map<string, { key: string; thoughts: Message[] }>();
    for (const g of turnGroups) {
      if (g.anchorId) m.set(g.anchorId, { key: g.key, thoughts: g.thoughts });
    }
    return m;
  }, [turnGroups]);

  const orphanThoughtGroups = useMemo(
    () =>
      turnGroups
        .filter((g) => !g.anchorId && g.thoughts.length > 0)
        .map((g) => ({ key: g.key, thoughts: g.thoughts })),
    [turnGroups],
  );

  const [openThinking, setOpenThinking] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const updates: Record<string, boolean> = {};
    for (const g of turnGroups) {
      const streaming = g.thoughts.some((m) => m.streaming);
      if (streaming && openThinking[g.key] !== true) {
        updates[g.key] = true;
      } else if (!streaming && !(g.key in openThinking)) {
        updates[g.key] = false;
      }
    }
    if (Object.keys(updates).length > 0) {
      setOpenThinking((prev) => ({ ...prev, ...updates }));
    }
  }, [turnGroups, openThinking]);
  const handleThinkingToggle = (panelKey: string, next: boolean) => {
    setOpenThinking((prev) => ({ ...prev, [panelKey]: next }));
  };
  const totalVisibleMessages = displayMessages.length;

  useLayoutEffect(() => {
    const last = displayMessages[displayMessages.length - 1];
    if (!last) return;

    const grew = totalVisibleMessages > prevLenRef.current;
    const behavior: ScrollBehavior = grew ? "smooth" : "auto";

    if (last.role === "user") {
      const el = messageRefs.current.get(last.id);
      if (el) {
        el.scrollIntoView({
          behavior,
          block: "start",
          inline: "nearest",
        });
      }
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior });
    }

    prevLenRef.current = totalVisibleMessages;
  }, [messages, totalVisibleMessages, displayMessages]);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const root = scrollContainerRef.current;
    const target = messagesEndRef.current;
    if (!root || !target) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        setShowScrollToBottom(!entry?.isIntersecting);
      },
      { root, threshold: 0, rootMargin: "0px 0px 120px 0px" },
    );
    io.observe(target);
    return () => io.disconnect();
  }, [messages, displayMessages.length]);

  return (
    <>
      <div
        className="aui-thread-root flex h-full min-h-0 min-w-0 flex-1 flex-col bg-background"
        style={{ ["--thread-max-width" as string]: "64rem" }}
      >
        <div
          ref={scrollContainerRef}
          className="aui-thread-viewport relative flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-scroll scroll-smooth scrollbar-hidden px-4 pt-4"
        >
          <div className="mx-auto flex min-h-0 w-full min-w-0 max-w-[var(--thread-max-width)] flex-1 flex-col px-3 sm:px-4">
          {messages.length === 0 ? (
            <WelcomeScreen
              agentName={welcomeAgentName}
              agentAvatar={welcomeAgentAvatar}
              description={welcomeDescription}
            />
          ) : (
        <>
        <div className="w-full space-y-3 sm:space-y-4">
        {displayMessages.map((message) => {
          if (message.category === "thought") {
            return null;
          }
          const isUser = message.role === "user";
          const anchoredGroup = !isUser
            ? thoughtsByAnchorId.get(message.id)
            : undefined;
          const interactionThoughts = anchoredGroup?.thoughts ?? [];
          const panelKey = anchoredGroup?.key ?? "";
          const userRootId = isUser ? userBranchRootId(message) : "";
          const userSnaps = isUser ? branchSnapshots[userRootId] : undefined;
          const userSnapCount = userSnaps?.length ?? 0;
          const userBranchSel =
            isUser && userSnapCount > 0
              ? branchVersionIndex[userRootId] ?? userSnapCount - 1
              : 0;
          const userOnLatestBranch =
            !isUser ||
            userSnapCount < 2 ||
            userBranchSel === userSnapCount - 1;

          const msgIdx = displayMessages.findIndex((m) => m.id === message.id);
          let assistantBranchRoot: string | null = null;
          if (!isUser && msgIdx >= 0) {
            for (let i = msgIdx - 1; i >= 0; i--) {
              if (displayMessages[i].role === "user") {
                assistantBranchRoot = userBranchRootId(displayMessages[i]);
                break;
              }
            }
          }
          const assistantSnaps =
            assistantBranchRoot && branchSnapshots[assistantBranchRoot]
              ? branchSnapshots[assistantBranchRoot]
              : undefined;
          const assistantSnapCount = assistantSnaps?.length ?? 0;
          const assistantBranchSel =
            assistantBranchRoot && assistantSnapCount > 0
              ? branchVersionIndex[assistantBranchRoot] ?? assistantSnapCount - 1
              : 0;

          return (
          <React.Fragment key={message.id}>
          {interactionThoughts.length > 0 && !isUser && (
            <ReasoningPanel
              key={`reasoning-${panelKey}`}
              panelKey={panelKey}
              interactionId={
                interactionThoughts[0]?.interactionId || message.interactionId || ""
              }
              thoughts={interactionThoughts}
              open={
                openThinking[panelKey] ??
                interactionThoughts.some((t) => t.streaming)
              }
              onToggle={handleThinkingToggle}
              variant="anchored"
            />
          )}
          <div
            className={
              isUser
                ? ""
                : "fade-in slide-in-from-bottom-1 animate-in duration-150"
            }
            data-message-category={message.category || "transcript"}
            data-message-role={message.role}
          >
            {isUser ? (
              <div
                ref={(el) => {
                  if (el) messageRefs.current.set(message.id, el);
                  else messageRefs.current.delete(message.id);
                }}
                className="aui-user-message-root fade-in slide-in-from-bottom-1 mx-auto grid w-full scroll-mt-4 animate-in auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 py-3 duration-150"
                data-role="user"
              >
                {message.attachments &&
                message.attachments.length > 0 &&
                editingMessageId !== message.id ? (
                  <div className="aui-user-message-attachments-end col-span-full col-start-1 row-start-1 mb-2 flex w-full flex-row justify-end gap-2">
                    <UserOutboundAttachments
                      messageId={message.id}
                      attachments={message.attachments}
                    />
                  </div>
                ) : null}
                <div
                  className={cn(
                    "aui-user-message-content-wrapper relative min-w-0 max-w-[85%] justify-self-end",
                    message.attachments?.length ? "row-start-2" : "row-start-1",
                    "group",
                  )}
                >
                    {editingMessageId === message.id ? (
                      <div className="aui-edit-composer-root ml-auto flex w-full flex-col rounded-2xl border border-zinc-200/90 bg-zinc-100 dark:border-white/10 dark:bg-zinc-800">
                        <textarea
                          value={editDraft}
                          onChange={(e) => setEditDraft(e.target.value)}
                          className="min-h-14 w-full resize-none bg-transparent p-4 text-sm text-zinc-900 outline-none dark:text-zinc-50"
                          rows={3}
                          aria-label="Edit message text"
                          autoFocus
                        />
                        <div className="mx-3 mb-3 flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setEditingMessageId(null);
                              setEditDraft("");
                            }}
                            className="rounded-md px-3 py-1.5 text-sm text-zinc-600 transition-colors hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-700"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={async () => {
                              await onEditMessage?.(message.id, editDraft);
                              setEditingMessageId(null);
                              setEditDraft("");
                            }}
                            className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm text-white transition-colors hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                          >
                            Update
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                      <div className="aui-user-message-content wrap-break-word rounded-2xl border border-zinc-200/90 bg-zinc-100 px-4 py-2.5 text-zinc-900 shadow-sm dark:border-white/10 dark:bg-zinc-800 dark:text-zinc-50">
                        <div className="break-words text-sm sm:text-base">
                          <MessageMediaBlock message={message} />
                          {message.content?.trim() ? (
                            <div className="markdown-content">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                code: ({
                                  inline,
                                  className,
                                  children,
                                  ...props
                                }: any) => {
                                  return !inline ? (
                                    <pre className="overflow-x-auto rounded-lg p-2 sm:p-3 my-2 bg-zinc-200/50 dark:bg-zinc-700/50 text-zinc-800 dark:text-zinc-200" {...props}>
                                      <code className={className} {...props}>{children}</code>
                                    </pre>
                                  ) : (
                                    <code className="px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200" {...props}>
                                      {children}
                                    </code>
                                  );
                                },
                                blockquote: ({ children, ...props }: any) => (
                                  <blockquote className="border-l-2 border-zinc-300 dark:border-zinc-600 pl-3 my-2 text-zinc-600 dark:text-zinc-400" {...props}>
                                    {children}
                                  </blockquote>
                                ),
                                a: ({ children, ...props }: any) => (
                                  <a className="underline text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100" {...props}>
                                    {children}
                                  </a>
                                ),
                                ul: ({ children, ...props }: any) => (
                                  <ul className="list-disc pl-4 sm:pl-6 my-2" {...props}>{children}</ul>
                                ),
                                ol: ({ children, ...props }: any) => (
                                  <ol className="list-decimal pl-4 sm:pl-6 my-2" {...props}>{children}</ol>
                                ),
                                h1: ({ children, ...props }: any) => (
                                  <h1 className="text-lg sm:text-xl font-bold my-2" {...props}>{children}</h1>
                                ),
                                h2: ({ children, ...props }: any) => (
                                  <h2 className="text-base sm:text-lg font-bold my-2" {...props}>{children}</h2>
                                ),
                                h3: ({ children, ...props }: any) => (
                                  <h3 className="text-sm sm:text-base font-semibold my-2" {...props}>{children}</h3>
                                ),
                                p: ({ children, ...props }: any) => {
                                  const extractText = (n: any): string => {
                                    if (typeof n === "string") return n;
                                    if (typeof n === "number") return String(n);
                                    if (React.isValidElement(n) && (n.props as any)?.children) {
                                      return extractText((n.props as any).children);
                                    }
                                    if (Array.isArray(n)) return n.map(extractText).join("");
                                    return "";
                                  };
                                  const childrenText = extractText(children);
                                  const isLastParagraph =
                                    message.streaming &&
                                    childrenText.trim() &&
                                    message.content.trim().endsWith(childrenText.trim());
                                  return (
                                    <p className="my-1 sm:my-2" {...props}>
                                      {children}
                                      {isLastParagraph && (
                                        <span className="inline-block w-[1.5px] h-[1em] ml-0.5 bg-zinc-900 dark:bg-zinc-50 animate-pulse align-text-bottom rounded-sm" />
                                      )}
                                    </p>
                                  );
                                },
                                table: ({ children, ...props }: any) => (
                                  <div className="overflow-x-auto my-2">
                                    <table className="border-collapse border border-zinc-300 dark:border-zinc-600" {...props}>{children}</table>
                                  </div>
                                ),
                                th: ({ children, ...props }: any) => (
                                  <th className="border border-zinc-300 dark:border-zinc-600 px-2 sm:px-4 py-1 sm:py-2 bg-zinc-200 dark:bg-zinc-700" {...props}>{children}</th>
                                ),
                                td: ({ children, ...props }: any) => (
                                  <td className="border border-zinc-300 dark:border-zinc-600 px-2 sm:px-4 py-1 sm:py-2" {...props}>{children}</td>
                                ),
                              }}
                            >
                              {message.content}
                            </ReactMarkdown>
                            </div>
                          ) : null}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center justify-between gap-2 sm:mt-2">
                          <div className="text-xs text-muted-foreground">
                            {new Date(message.timestamp).toLocaleTimeString()}
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            {(() => {
                              const shouldShowDebug =
                                (message.debugData ||
                                (message.role === "user" &&
                                  message.interactionId &&
                                  (() => {
                                    const messagesForInteraction = messages.filter(
                                      (m) =>
                                        m.role === "user" &&
                                        m.interactionId === message.interactionId,
                                    );
                                    const lastMessageForInteraction =
                                      messagesForInteraction[
                                        messagesForInteraction.length - 1
                                      ];
                                    return lastMessageForInteraction?.id === message.id;
                                  })()));
                              return shouldShowDebug ? (
                                <button
                                  onClick={() => {
                                    const debugMessageForInteraction = messages.find(
                                      (m) =>
                                        m.interactionId === message.interactionId &&
                                        m.debugData,
                                    );
                                    setDebugMessage(debugMessageForInteraction || message);
                                  }}
                                  className="text-xs px-2 py-1 rounded-md bg-zinc-200 hover:bg-zinc-300 text-zinc-600 dark:bg-zinc-700 dark:hover:bg-zinc-600 dark:text-zinc-300 touch-manipulation transition-colors"
                                >
                                  Debug
                                </button>
                              ) : null;
                            })()}
                          </div>
                        </div>
                      </div>
                      {onEditMessage &&
                      !message.attachments?.length &&
                      !isStreaming &&
                      userOnLatestBranch &&
                      editingMessageId !== message.id ? (
                        <div className="aui-user-action-bar-wrapper absolute top-1/2 left-0 z-10 flex flex-col items-end -translate-x-full -translate-y-1/2 pr-2 md:opacity-0 md:transition-opacity md:group-hover:opacity-100">
                          <button
                            type="button"
                            onClick={() => {
                              setEditingMessageId(message.id);
                              setEditDraft(message.content ?? "");
                            }}
                            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-4 text-muted-foreground transition-colors hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800"
                            aria-label="Edit message"
                          >
                            <Pencil className="size-4 stroke-[1.5px]" strokeWidth={1.5} aria-hidden />
                          </button>
                        </div>
                      ) : null}
                      </>
                    )}
                </div>
                {userSnapCount >= 2 && onBranchVersionChange ? (
                  <div className="aui-branch-picker-root col-span-full col-start-1 row-start-3 -mr-1 ml-auto flex justify-end">
                    <div className="mr-2 -ml-2 inline-flex items-center text-muted-foreground text-xs">
                      <button
                        type="button"
                        className="inline-flex min-h-[32px] min-w-[32px] items-center justify-center rounded-md p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        aria-label="Previous version"
                        onClick={() =>
                          onBranchVersionChange(
                            userRootId,
                            Math.max(0, userBranchSel - 1),
                          )
                        }
                      >
                        <ChevronLeft className="size-4" aria-hidden />
                      </button>
                      <span className="font-medium tabular-nums">
                        {userBranchSel + 1} / {userSnapCount}
                      </span>
                      <button
                        type="button"
                        className="inline-flex min-h-[32px] min-w-[32px] items-center justify-center rounded-md p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        aria-label="Next version"
                        onClick={() =>
                          onBranchVersionChange(
                            userRootId,
                            Math.min(userSnapCount - 1, userBranchSel + 1),
                          )
                        }
                      >
                        <ChevronRight className="size-4" aria-hidden />
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              /* Assistant: transparent, no bubble */
              <div className="aui-assistant-message-root fade-in slide-in-from-bottom-1 relative mx-auto w-full animate-in py-3 duration-150">
                <div className="px-2 text-foreground leading-relaxed">
                  <div className="break-words text-sm sm:text-base">
                    <MessageMediaBlock message={message} />
                    {message.content?.trim() ? (
                      <div
                        className="aui-md markdown-content"
                        data-status={message.streaming ? "running" : undefined}
                      >
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          code: ({
                            inline,
                            className,
                            children,
                            ...props
                          }: any) => {
                            return !inline ? (
                              <pre className="overflow-x-auto rounded-lg p-2 sm:p-3 my-2 bg-zinc-100 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200" {...props}>
                                <code className={className} {...props}>{children}</code>
                              </pre>
                            ) : (
                              <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200" {...props}>
                                {children}
                              </code>
                            );
                          },
                          blockquote: ({ children, ...props }: any) => (
                            <blockquote className="border-l-2 border-zinc-300 dark:border-zinc-600 pl-3 my-2 text-zinc-600 dark:text-zinc-400" {...props}>
                              {children}
                            </blockquote>
                          ),
                          a: ({ children, ...props }: any) => (
                            <a className="underline text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100" {...props}>
                              {children}
                            </a>
                          ),
                          ul: ({ children, ...props }: any) => (
                            <ul className="list-disc pl-4 sm:pl-6 my-2" {...props}>{children}</ul>
                          ),
                          ol: ({ children, ...props }: any) => (
                            <ol className="list-decimal pl-4 sm:pl-6 my-2" {...props}>{children}</ol>
                          ),
                          h1: ({ children, ...props }: any) => (
                            <h1 className="text-lg sm:text-xl font-bold my-2" {...props}>{children}</h1>
                          ),
                          h2: ({ children, ...props }: any) => (
                            <h2 className="text-base sm:text-lg font-bold my-2" {...props}>{children}</h2>
                          ),
                          h3: ({ children, ...props }: any) => (
                            <h3 className="text-sm sm:text-base font-semibold my-2" {...props}>{children}</h3>
                          ),
                          p: ({ children, ...props }: any) => (
                            <p className="my-1 sm:my-2" {...props}>
                              {children}
                            </p>
                          ),
                          table: ({ children, ...props }: any) => (
                            <div className="overflow-x-auto my-2">
                              <table className="border-collapse border border-zinc-300 dark:border-zinc-600" {...props}>{children}</table>
                            </div>
                          ),
                          th: ({ children, ...props }: any) => (
                            <th className="border border-zinc-300 dark:border-zinc-600 px-2 sm:px-4 py-1 sm:py-2 bg-zinc-100 dark:bg-zinc-800" {...props}>{children}</th>
                          ),
                          td: ({ children, ...props }: any) => (
                            <td className="border border-zinc-300 dark:border-zinc-600 px-2 sm:px-4 py-1 sm:py-2" {...props}>{children}</td>
                          ),
                        }}
                      >
                        {message.content}
                      </ReactMarkdown>
                      </div>
                    ) : null}
                  </div>
                  {message.streaming && !message.content?.trim() && (
                    <div className="flex w-full flex-col gap-2 pt-2 pb-1">
                      <div className="animate-pulse h-4 w-3/4 rounded bg-zinc-200/50 dark:bg-zinc-700/50" />
                      <div className="animate-pulse h-4 w-1/2 rounded bg-zinc-200/50 dark:bg-zinc-700/50" />
                    </div>
                  )}
                </div>
                <div className="aui-assistant-message-footer mt-1 ml-2 flex flex-wrap items-center gap-1 sm:gap-2">
                  {assistantSnapCount >= 2 &&
                  assistantBranchRoot &&
                  onBranchVersionChange ? (
                    <div className="aui-branch-picker-root mr-2 -ml-2 inline-flex items-center text-muted-foreground text-xs">
                      <button
                        type="button"
                        className="inline-flex min-h-[32px] min-w-[32px] items-center justify-center rounded-md p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        aria-label="Previous version"
                        onClick={() =>
                          onBranchVersionChange(
                            assistantBranchRoot,
                            Math.max(0, assistantBranchSel - 1),
                          )
                        }
                      >
                        <ChevronLeft className="size-4" aria-hidden />
                      </button>
                      <span className="font-medium tabular-nums">
                        {assistantBranchSel + 1} / {assistantSnapCount}
                      </span>
                      <button
                        type="button"
                        className="inline-flex min-h-[32px] min-w-[32px] items-center justify-center rounded-md p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        aria-label="Next version"
                        onClick={() =>
                          onBranchVersionChange(
                            assistantBranchRoot,
                            Math.min(
                              assistantSnapCount - 1,
                              assistantBranchSel + 1,
                            ),
                          )
                        }
                      >
                        <ChevronRight className="size-4" aria-hidden />
                      </button>
                    </div>
                  ) : null}
                  <div className="text-xs text-muted-foreground">
                    {new Date(message.timestamp).toLocaleTimeString()}
                  </div>
                  {!message.streaming && message.content?.trim() ? (
                    <CopyButton content={message.content} />
                  ) : null}
                  {(() => {
                    const shouldShowDebug =
                      (message.debugData ||
                      (message.role === "assistant" &&
                        message.interactionId &&
                        (() => {
                          const messagesForInteraction = messages.filter(
                            (m) =>
                              m.role === "assistant" &&
                              m.interactionId === message.interactionId,
                          );
                          const lastMessageForInteraction =
                            messagesForInteraction[
                              messagesForInteraction.length - 1
                            ];
                          return lastMessageForInteraction?.id === message.id;
                        })()));
                    return shouldShowDebug ? (
                      <button
                        onClick={() => {
                          const debugMessageForInteraction = messages.find(
                            (m) =>
                              m.interactionId === message.interactionId &&
                              m.debugData,
                          );
                          setDebugMessage(debugMessageForInteraction || message);
                        }}
                        className="text-xs px-2 py-1 rounded-md bg-zinc-100 hover:bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:hover:bg-zinc-700 dark:text-zinc-400 touch-manipulation transition-colors"
                      >
                        Debug
                      </button>
                    ) : null;
                  })()}
                </div>
              </div>
            )}
          </div>
          </React.Fragment>
          );
        })}

        </div>

        {orphanThoughtGroups.map(({ key, thoughts }) => (
          <ReasoningPanel
            key={`orphan-reasoning-${key}`}
            panelKey={key}
            interactionId={thoughts[0]?.interactionId || ""}
            thoughts={thoughts}
            open={openThinking[key] ?? thoughts.some((t) => t.streaming)}
            onToggle={handleThinkingToggle}
            variant="orphan"
          />
        ))}

        {messages.length > 0 && showThinking && (
          <div className="fade-in slide-in-from-bottom-1 animate-in duration-150">
            <div className="w-full py-3">
              <div className="px-2">
                <div className="flex w-full flex-col gap-2 pt-2 pb-1">
                  <div className="animate-pulse h-4 w-3/4 rounded bg-zinc-200/50 dark:bg-zinc-700/50" />
                  <div className="animate-pulse h-4 w-1/2 rounded bg-zinc-200/50 dark:bg-zinc-700/50" />
                </div>
              </div>
            </div>
          </div>
        )}

          </>
          )}
        <div ref={messagesEndRef} className="h-px w-full shrink-0" aria-hidden />

        <div className="aui-thread-viewport-footer sticky bottom-0 z-10 mt-auto flex w-full flex-col gap-4 overflow-visible rounded-t-3xl bg-background pb-4 md:pb-6">
          <div className="relative flex min-h-[3rem] justify-center">
            {showScrollToBottom ? (
              <button
                type="button"
                onClick={() =>
                  messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
                }
                className="absolute -top-12 z-10 rounded-full border border-border bg-background p-4 shadow-sm transition-colors hover:bg-muted dark:bg-background dark:hover:bg-muted"
                aria-label="Scroll to bottom"
              >
                <ArrowDown className="size-4" aria-hidden />
              </button>
            ) : null}
          </div>
          <MessageInput
            onSend={onSend}
            disabled={composerDisabled}
            placeholder={placeholder}
            variant="thread"
            composerMenu={composerMenu}
            onStop={onStop}
          />
        </div>
          </div>
      </div>
      </div>

      {/* Debug Modal */}
      {debugMessage && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4"
          onClick={() => setDebugMessage(null)}
        >
          <div
            className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-white/10 max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-4 sm:px-6 py-3 border-b border-zinc-200 dark:border-white/10 flex items-center justify-between flex-shrink-0">
              <h3 className="text-base sm:text-lg font-semibold text-zinc-900 dark:text-zinc-50">
                Debug View - Message {debugMessage.id.substring(0, 20)}...
              </h3>
              <button
                onClick={() => setDebugMessage(null)}
                className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 text-2xl touch-manipulation p-1 transition-colors duration-150"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 sm:p-6">
              <div className="mb-4">
                <h4 className="text-xs sm:text-sm font-semibold text-zinc-600 dark:text-zinc-300 mb-2">
                  Message Content:
                </h4>
                {debugModalPayload.parsed != null ? (
                  <JsonViewer
                    data={debugModalPayload.parsed}
                    dark={jsonPanelDark}
                    defaultExpandDepth={2}
                    maxHeight="40vh"
                  />
                ) : (
                  <div
                    className={cn(
                      "rounded-lg border p-2 sm:p-3",
                      jsonPanelDark
                        ? "bg-black border-zinc-800"
                        : "bg-zinc-100 border-zinc-300",
                    )}
                  >
                    <pre
                      className={cn(
                        "whitespace-pre-wrap text-xs sm:text-sm",
                        jsonPanelDark ? "text-zinc-200" : "text-zinc-800",
                      )}
                    >
                      {debugModalPayload.raw}
                    </pre>
                  </div>
                )}
              </div>
              {debugMessage.debugData ? (
                <div>
                  <h4 className="text-xs sm:text-sm font-semibold text-zinc-600 dark:text-zinc-300 mb-2">
                    Full JSON Response (type=final):
                  </h4>
                  <JsonViewer
                    data={debugMessage.debugData}
                    defaultExpandDepth={2}
                    dark={jsonPanelDark}
                    maxHeight="60vh"
                  />
                </div>
              ) : (
                <div className="text-xs sm:text-sm text-zinc-400 dark:text-zinc-500 italic">
                  Debug data not available yet. Waiting for final interaction
                  data...
                </div>
              )}
            </div>
            <div className="px-4 sm:px-6 py-3 border-t border-zinc-200 dark:border-white/10 flex justify-end flex-shrink-0">
              <button
                onClick={() => {
                  navigator.clipboard.writeText(
                    JSON.stringify(debugMessage.debugData, null, 2),
                  );
                }}
                className="px-4 py-2 bg-zinc-900 text-white rounded-lg hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 touch-manipulation text-sm sm:text-base transition-colors duration-150"
              >
                Copy JSON
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
