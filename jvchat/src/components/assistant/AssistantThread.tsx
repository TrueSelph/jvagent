/**
 * assistant-ui powered chat thread for jvchat — using assistant-ui's official
 * styled component system (`@assistant-ui/styles`, the `aui-*` class layer) so
 * the surface looks and feels like a native assistant-ui Thread.
 *
 * jvchat still owns the data plane through an ExternalStore adapter over
 * `useStreaming` (SSE, sessions, branch snapshots, debugData):
 *   - composer is assistant-ui's own (`aui-composer-*`); attachments are wired
 *     through a custom AttachmentAdapter that hands the original File objects
 *     back to jvchat's send pipeline. jvchat's tools menu is kept inline.
 *   - native ActionBar.Edit + edit composer route to `editAndResend`.
 *   - branch navigation is rendered with assistant-ui's branch-picker styling
 *     but drives jvchat's `selectBranchVersion` (the external store exposes no
 *     branch-switch callback).
 *   - per-message **Debug** link after every completed answer opens the legacy
 *     debug dialog from the final-chunk debugData on message metadata.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessage,
  useMessageAttachment,
  type AppendMessage,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
  type ReasoningMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  Check,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CheckIcon,
  Copy,
  Loader2Icon,
  Paperclip,
  Pencil,
  SparklesIcon,
  SquareIcon,
  WrenchIcon,
  X,
} from "lucide-react";

import type { SendMessageOptions } from "../../hooks/useStreaming";
import { cn } from "../../lib/utils";
import {
  buildThreadMessages,
  type JvAssistantMeta,
  type JvUserMeta,
} from "../../lib/threadMessages";
import type { Message } from "../../types/message";
import { MessageDebugDialog } from "./MessageDebugDialog";

export interface AssistantThreadProps {
  messages: Message[];
  isStreaming?: boolean;
  onEditMessage?: (
    messageId: string,
    newContent: string,
  ) => void | Promise<void>;
  branchSnapshots?: Record<string, Message[][]>;
  branchVersionIndex?: Record<string, number>;
  onBranchVersionChange?: (rootId: string, index: number) => void;
  onSend: (message: string, options?: SendMessageOptions) => void;
  onStop?: () => void;
  placeholder?: string;
  composerDisabled?: boolean;
  composerMenu?: React.ReactNode;
  welcomeAgentName?: string;
  welcomeAgentAvatar?: string;
  welcomeDescription?: string;
}

// --- shared button classes (assistant-ui ghost / primary icon buttons) ------

const ICON_BTN =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-md " +
  "text-[color:var(--color-muted-foreground)] transition-colors " +
  "hover:bg-[color:var(--color-accent)] hover:text-[color:var(--color-accent-foreground)] " +
  "disabled:pointer-events-none disabled:opacity-40";

const PRIMARY_BTN =
  "inline-flex items-center justify-center " +
  "bg-[color:var(--color-foreground)] text-[color:var(--color-background)] " +
  "transition-opacity hover:opacity-90 disabled:opacity-40";

// --- jvchat bridge context (branch state + debug opener) --------------------

interface JvThreadCtx {
  branchSnapshots: Record<string, Message[][]>;
  branchVersionIndex: Record<string, number>;
  onBranchVersionChange?: (rootId: string, index: number) => void;
  openDebug: (m: Message | null) => void;
}

const JvThreadContext = createContext<JvThreadCtx | null>(null);
const useJvThread = (): JvThreadCtx => {
  const ctx = useContext(JvThreadContext);
  if (!ctx) throw new Error("JvThreadContext missing");
  return ctx;
};

// --- attachment adapter (assistant-ui composer -> jvchat File[] send) --------

const FILE_STORE = new Map<string, File>();

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const jvAttachmentAdapter: AttachmentAdapter = {
  accept: "image/*,application/pdf,text/*",
  async add({ file }): Promise<PendingAttachment> {
    const id =
      globalThis.crypto?.randomUUID?.() ??
      `att-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    FILE_STORE.set(id, file);
    return {
      id,
      type: file.type.startsWith("image/") ? "image" : "document",
      name: file.name,
      contentType: file.type,
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  },
  async send(attachment): Promise<CompleteAttachment> {
    const file = FILE_STORE.get(attachment.id);
    const isImage = (file?.type ?? attachment.contentType ?? "").startsWith(
      "image/",
    );
    const dataUrl = file && isImage ? await fileToDataUrl(file) : "";
    return {
      ...attachment,
      status: { type: "complete" },
      content: dataUrl
        ? [{ type: "image", image: dataUrl }]
        : [{ type: "text", text: attachment.name }],
    };
  },
  async remove(attachment) {
    FILE_STORE.delete(attachment.id);
  },
};

function appendMessageText(message: AppendMessage): string {
  return message.content
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("")
    .trim();
}

function appendMessageFiles(message: AppendMessage): File[] {
  const files: File[] = [];
  for (const a of message.attachments ?? []) {
    const f = FILE_STORE.get(a.id);
    if (f) files.push(f);
    FILE_STORE.delete(a.id);
  }
  return files;
}

// --- markdown (aui-md-* classes) --------------------------------------------

function omitNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const clone = { ...props };
  delete (clone as { node?: unknown }).node;
  return clone;
}

const MD_COMPONENTS: Components = {
  p: (p) => <p className="aui-md-p" {...omitNode(p)} />,
  a: (p) => (
    <a
      className="aui-md-a"
      target="_blank"
      rel="noreferrer noopener"
      {...omitNode(p)}
    />
  ),
  ul: (p) => <ul className="aui-md-ul" {...omitNode(p)} />,
  ol: (p) => <ol className="aui-md-ol" {...omitNode(p)} />,
  li: (p) => <li className="aui-md-li" {...omitNode(p)} />,
  blockquote: (p) => <blockquote className="aui-md-blockquote" {...omitNode(p)} />,
  h1: (p) => <h1 className="aui-md-h1" {...omitNode(p)} />,
  h2: (p) => <h2 className="aui-md-h2" {...omitNode(p)} />,
  h3: (p) => <h3 className="aui-md-h3" {...omitNode(p)} />,
  h4: (p) => <h4 className="aui-md-h4" {...omitNode(p)} />,
  hr: (p) => <hr className="aui-md-hr" {...omitNode(p)} />,
  table: (p) => <table className="aui-md-table" {...omitNode(p)} />,
  th: (p) => <th className="aui-md-th" {...omitNode(p)} />,
  td: (p) => <td className="aui-md-td" {...omitNode(p)} />,
  tr: (p) => <tr className="aui-md-tr" {...omitNode(p)} />,
  code: (p) => <code className="aui-md-inline-code" {...omitNode(p)} />,
  pre: (p) => <pre className="aui-md-pre" {...omitNode(p)} />,
};

/** Assistant body — canonical assistant-ui smooth-streaming markdown. */
const AssistantMarkdown: TextMessagePartComponent = () => (
  <MarkdownTextPrimitive
    smooth
    remarkPlugins={[remarkGfm]}
    components={MD_COMPONENTS}
  />
);

/** User body — plain text (no markdown), matching assistant-ui user bubbles. */
const UserText: TextMessagePartComponent = ({ text }) => (
  <span className="whitespace-pre-wrap break-words">{text}</span>
);

// --- reasoning (collapsible, aui-reasoning-*) -------------------------------

const ReasoningText: ReasoningMessagePartComponent = ({ text }) => {
  if (!text?.trim()) return null;
  return <div className="whitespace-pre-wrap break-words">{text}</div>;
};

/** Three pulsing dots — assistant-ui style "thinking" indicator. */
const ThinkingDots = () => (
  <div className="flex items-center gap-1 py-1 text-[color:var(--color-muted-foreground)]">
    <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
    <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
    <span className="size-1.5 animate-bounce rounded-full bg-current" />
  </div>
);

const ReasoningGroup = ({ children }: { children?: React.ReactNode }) => {
  const running = useMessage((m) => m.status?.type === "running");
  const [open, setOpen] = useState<boolean>(running);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) setOpen(false);
    wasRunning.current = running;
  }, [running]);
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="aui-reasoning-trigger hover:text-[color:var(--color-foreground)]"
        data-state={open ? "open" : "closed"}
      >
        {running ? (
          <Loader2Icon
            className="aui-reasoning-trigger-icon size-4 animate-spin"
            aria-hidden
          />
        ) : (
          <SparklesIcon
            className="aui-reasoning-trigger-icon size-4"
            aria-hidden
          />
        )}
        <span className="aui-reasoning-trigger-label-wrapper">
          <span className={running ? "aui-reasoning-trigger-shimmer" : undefined}>
            {running ? "Thinking" : "Reasoning"}
          </span>
        </span>
        <ChevronDownIcon
          className={cn(
            "aui-reasoning-trigger-chevron size-4 transition-transform",
            open ? "" : "-rotate-90",
          )}
          aria-hidden
        />
      </button>
      {open ? (
        <div className="aui-reasoning-content">
          <div className="aui-reasoning-text">{children}</div>
        </div>
      ) : null}
    </div>
  );
};

// --- tool call (aui-tool-fallback-*) ----------------------------------------

function formatToolResult(result: unknown): string {
  if (result == null) return "";
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

const ToolPart: ToolCallMessagePartComponent = ({
  toolName,
  args,
  result,
  isError,
  status,
}) => {
  const [open, setOpen] = useState(false);
  const argStr =
    args && Object.keys(args).length ? JSON.stringify(args, null, 2) : "";
  const resStr = formatToolResult(result);
  const running = status?.type === "running";
  const done = result !== undefined || status?.type === "complete";
  return (
    <div className="aui-tool-fallback-root my-2 border-[color:var(--color-border)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="aui-tool-fallback-trigger hover:text-[color:var(--color-foreground)]"
      >
        {running ? (
          <Loader2Icon
            className="aui-tool-fallback-trigger-icon size-4 animate-spin"
            aria-hidden
          />
        ) : isError ? (
          <WrenchIcon
            className="aui-tool-fallback-trigger-icon size-4 text-[color:var(--color-destructive)]"
            aria-hidden
          />
        ) : done ? (
          <CheckIcon
            className="aui-tool-fallback-trigger-icon size-4"
            aria-hidden
          />
        ) : (
          <WrenchIcon
            className="aui-tool-fallback-trigger-icon size-4"
            aria-hidden
          />
        )}
        <span className="aui-tool-fallback-trigger-label-wrapper">
          {running ? "Running" : "Used"} tool:{" "}
          <b className="font-mono">{toolName}</b>
        </span>
        <ChevronDownIcon
          className={cn(
            "aui-tool-fallback-trigger-chevron ml-auto size-4 transition-transform",
            open ? "" : "-rotate-90",
          )}
          aria-hidden
        />
      </button>
      {open && (argStr || resStr) ? (
        <div className="aui-tool-fallback-content space-y-1 px-4 pt-2">
          {argStr ? (
            <pre className="aui-tool-fallback-args max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[color:var(--color-muted)] p-2 text-xs">
              {argStr}
            </pre>
          ) : null}
          {resStr ? (
            <pre className="aui-tool-fallback-result max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-[color:var(--color-muted)] p-2 text-xs">
              {resStr}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

// --- footer controls --------------------------------------------------------

function BranchPicker({ rootId }: { rootId?: string }) {
  const { branchSnapshots, branchVersionIndex, onBranchVersionChange } =
    useJvThread();
  if (!rootId) return null;
  const snaps = branchSnapshots[rootId];
  const count = snaps?.length ?? 0;
  if (count < 2) return null;
  const index = branchVersionIndex[rootId] ?? count - 1;
  return (
    <div className="aui-branch-picker-root">
      <button
        type="button"
        aria-label="Previous"
        disabled={index <= 0}
        onClick={() => onBranchVersionChange?.(rootId, index - 1)}
        className={ICON_BTN + " size-6"}
      >
        <ChevronLeftIcon className="size-4" aria-hidden />
      </button>
      <span className="aui-branch-picker-state">
        {index + 1} / {count}
      </span>
      <button
        type="button"
        aria-label="Next"
        disabled={index >= count - 1}
        onClick={() => onBranchVersionChange?.(rootId, index + 1)}
        className={ICON_BTN + " size-6"}
      >
        <ChevronRightIcon className="size-4" aria-hidden />
      </button>
    </div>
  );
}

function DebugButton() {
  const { openDebug } = useJvThread();
  const meta = useMessage(
    (m) => m.metadata?.custom as unknown as JvAssistantMeta | undefined,
  );
  const debugMessage = meta?.debugMessage ?? null;
  if (!debugMessage) return null;
  return (
    <button
      type="button"
      onClick={() => openDebug(debugMessage)}
      className="text-xs text-[color:var(--color-muted-foreground)] underline-offset-2 transition-colors hover:text-[color:var(--color-foreground)] hover:underline"
    >
      Debug
    </button>
  );
}

// --- message renderers ------------------------------------------------------

const AssistantMessage = () => {
  const meta = useMessage(
    (m) => m.metadata?.custom as unknown as JvAssistantMeta | undefined,
  );
  const thinking = useMessage(
    (m) =>
      m.status?.type === "running" &&
      !m.content.some(
        (p) => p.type === "text" && (p as { text?: string }).text?.trim(),
      ),
  );
  return (
    <MessagePrimitive.Root className="aui-assistant-message-root">
      <div className="aui-assistant-message-content">
        <MessagePrimitive.Parts
          components={{
            Text: AssistantMarkdown,
            Reasoning: ReasoningText,
            ReasoningGroup,
            tools: { Fallback: ToolPart },
          }}
        />
        {thinking ? <ThinkingDots /> : null}
      </div>
      <div className="aui-assistant-message-footer">
        <BranchPicker rootId={meta?.branchRootId} />
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          autohideFloat="single-branch"
          className="aui-assistant-action-bar-root"
        >
          <ActionBarPrimitive.Copy className={ICON_BTN}>
            <MessagePrimitive.If copied>
              <Check className="size-4" aria-hidden />
            </MessagePrimitive.If>
            <MessagePrimitive.If copied={false}>
              <Copy className="size-4" aria-hidden />
            </MessagePrimitive.If>
          </ActionBarPrimitive.Copy>
          <DebugButton />
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
};

const UserAttachment = () => {
  const attachment = useMessageAttachment((a) => a);
  const parts = (attachment?.content ?? []) as Array<{
    type: string;
    image?: string;
    text?: string;
  }>;
  const image = parts.find((p) => p.type === "image")?.image;
  if (image) {
    return (
      <img
        src={image}
        alt={attachment?.name ?? ""}
        className="aui-attachment-tile-image max-h-48 w-auto max-w-[12rem] rounded-xl border border-[color:var(--color-border)] object-contain"
      />
    );
  }
  return (
    <span className="aui-attachment-tile inline-flex items-center gap-2 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-2 py-1.5 text-xs">
      <span className="truncate max-w-[12rem]" title={attachment?.name}>
        {attachment?.name ?? "attachment"}
      </span>
    </span>
  );
};

const UserMessage = () => {
  const meta = useMessage(
    (m) => m.metadata?.custom as unknown as JvUserMeta | undefined,
  );
  return (
    <MessagePrimitive.Root className="aui-user-message-root">
      <div className="aui-user-message-attachments-end col-start-2 flex flex-col items-end gap-2">
        <MessagePrimitive.Attachments
          components={{ Image: UserAttachment, File: UserAttachment }}
        />
      </div>
      <div className="aui-user-message-content-wrapper">
        <div className="aui-user-message-content">
          <MessagePrimitive.Parts components={{ Text: UserText }} />
        </div>
      </div>
      <div className="aui-user-action-bar-root col-start-1 row-start-2 mr-3 mt-2 flex justify-end">
        <BranchPicker rootId={meta?.branchRootId} />
        <ActionBarPrimitive.Root hideWhenRunning autohide="not-last">
          <ActionBarPrimitive.Edit
            className={ICON_BTN}
            aria-label="Edit"
          >
            <Pencil className="size-4" aria-hidden />
          </ActionBarPrimitive.Edit>
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
};

const UserEditComposer = () => (
  <MessagePrimitive.Root className="aui-edit-composer-wrapper mx-auto w-full max-w-[var(--thread-max-width)] px-2 py-3">
    <ComposerPrimitive.Root className="aui-edit-composer-root">
      <ComposerPrimitive.Input
        autoFocus
        className="aui-edit-composer-input"
      />
      <div className="aui-edit-composer-footer flex justify-end gap-2 p-2">
        <ComposerPrimitive.Cancel className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs text-[color:var(--color-muted-foreground)] hover:bg-[color:var(--color-accent)]">
          <X className="size-3.5" aria-hidden /> Cancel
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send
          className={
            PRIMARY_BTN + " gap-1 rounded-md px-3 py-1.5 text-xs"
          }
        >
          <Check className="size-3.5" aria-hidden /> Save
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  </MessagePrimitive.Root>
);

// --- composer ---------------------------------------------------------------

const ComposerAttachment = () => (
  <AttachmentPrimitive.Root className="aui-attachment-root-composer relative flex items-center gap-1.5 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-2 py-1.5 text-xs">
    <AttachmentPrimitive.Name />
    <AttachmentPrimitive.Remove className="aui-attachment-remove-icon ml-1 text-[color:var(--color-muted-foreground)] hover:text-[color:var(--color-foreground)]">
      <X className="size-3.5" aria-hidden />
    </AttachmentPrimitive.Remove>
  </AttachmentPrimitive.Root>
);

function Composer({
  placeholder,
  composerMenu,
}: {
  placeholder: string;
  composerMenu?: React.ReactNode;
}) {
  return (
    <ComposerPrimitive.Root className="aui-composer-root rounded-3xl border border-[color:var(--color-border)] bg-[color:var(--color-background)] shadow-sm transition-colors focus-within:border-[color:var(--color-ring,var(--color-border))]">
      <ComposerPrimitive.Attachments
        components={{ Attachment: ComposerAttachment }}
      />
      <ComposerPrimitive.Input
        autoFocus
        rows={1}
        placeholder={placeholder}
        className="aui-composer-input"
      />
      <div className="aui-composer-action-wrapper">
        <div className="flex items-center gap-1">
          <ComposerPrimitive.AddAttachment
            className={ICON_BTN}
            aria-label="Attach"
          >
            <Paperclip className="size-5" aria-hidden />
          </ComposerPrimitive.AddAttachment>
          {composerMenu}
        </div>
        <ThreadPrimitive.If running={false}>
          <ComposerPrimitive.Send
            className={PRIMARY_BTN + " aui-composer-send"}
            aria-label="Send"
          >
            <ArrowUpIcon className="size-5" aria-hidden />
          </ComposerPrimitive.Send>
        </ThreadPrimitive.If>
        <ThreadPrimitive.If running>
          <ComposerPrimitive.Cancel
            className={PRIMARY_BTN + " aui-composer-cancel"}
            aria-label="Stop"
          >
            <SquareIcon className="size-4 fill-current" aria-hidden />
          </ComposerPrimitive.Cancel>
        </ThreadPrimitive.If>
      </div>
    </ComposerPrimitive.Root>
  );
}

// --- main component ---------------------------------------------------------

export function AssistantThread({
  messages,
  isStreaming = false,
  onEditMessage,
  branchSnapshots = {},
  branchVersionIndex = {},
  onBranchVersionChange,
  onSend,
  onStop,
  placeholder = "Send a message...",
  composerDisabled = false,
  composerMenu,
  welcomeAgentName = "Agent",
  welcomeAgentAvatar,
  welcomeDescription,
}: AssistantThreadProps) {
  const [debugMessage, setDebugMessage] = useState<Message | null>(null);

  const threadMessages = useMemo(
    () => buildThreadMessages(messages),
    [messages],
  );

  const runtime = useExternalStoreRuntime({
    messages: threadMessages,
    convertMessage: (m) => m,
    isRunning: isStreaming,
    isDisabled: composerDisabled,
    onNew: async (message: AppendMessage) => {
      const text = appendMessageText(message);
      const files = appendMessageFiles(message);
      if (text || files.length) {
        onSend(text, files.length ? { files } : undefined);
      }
    },
    onEdit: async (message: AppendMessage) => {
      const text = appendMessageText(message);
      const editedId = message.sourceId;
      if (text && editedId) await onEditMessage?.(editedId, text);
    },
    onCancel: async () => {
      onStop?.();
    },
    adapters: { attachments: jvAttachmentAdapter },
    unstable_capabilities: { copy: true },
  });

  const jvCtx = useMemo<JvThreadCtx>(
    () => ({
      branchSnapshots,
      branchVersionIndex,
      onBranchVersionChange,
      openDebug: setDebugMessage,
    }),
    [branchSnapshots, branchVersionIndex, onBranchVersionChange],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <JvThreadContext.Provider value={jvCtx}>
        <ThreadPrimitive.Root
          className="aui-thread-root flex h-full min-h-0 flex-col bg-[color:var(--color-background)]"
          style={
            { ["--thread-max-width" as string]: "44rem" } as React.CSSProperties
          }
        >
          <ThreadPrimitive.Viewport className="aui-thread-viewport">
            <ThreadPrimitive.Empty>
              <div className="aui-thread-welcome-root">
                <div className="aui-thread-welcome-center">
                  <div className="aui-thread-welcome-message items-center text-center">
                    {welcomeAgentAvatar ? (
                      <img
                        src={welcomeAgentAvatar}
                        alt={welcomeAgentName}
                        className="mb-4 size-14 rounded-full object-cover"
                      />
                    ) : null}
                    <p className="aui-thread-welcome-message-inner">
                      Hello! I&apos;m {welcomeAgentName}
                    </p>
                    {welcomeDescription ? (
                      <p className="mt-2 max-w-xl text-sm text-[color:var(--color-muted-foreground)]">
                        {welcomeDescription}
                      </p>
                    ) : null}
                  </div>
                </div>
              </div>
            </ThreadPrimitive.Empty>

            <ThreadPrimitive.Messages
              components={{ UserMessage, AssistantMessage, UserEditComposer }}
            />

            <ThreadPrimitive.If empty={false}>
              <div className="min-h-6 flex-grow" />
            </ThreadPrimitive.If>

            <div className="aui-thread-viewport-footer">
              <ThreadPrimitive.ScrollToBottom
                className={
                  ICON_BTN +
                  " aui-thread-scroll-to-bottom bg-[color:var(--color-background)]"
                }
              >
                <ArrowDownIcon className="size-4" aria-hidden />
              </ThreadPrimitive.ScrollToBottom>
              <Composer placeholder={placeholder} composerMenu={composerMenu} />
            </div>
          </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>

        <MessageDebugDialog
          message={debugMessage}
          onClose={() => setDebugMessage(null)}
        />
      </JvThreadContext.Provider>
    </AssistantRuntimeProvider>
  );
}

export default AssistantThread;
