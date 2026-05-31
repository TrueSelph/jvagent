/**
 * assistant-ui powered chat thread for jvchat.
 *
 * Renders the conversation with @assistant-ui/react primitives while keeping
 * jvchat's own plumbing as the source of truth:
 *   - `useStreaming` still owns SSE, sessions, branch snapshots and debugData;
 *     we feed it in through an ExternalStore adapter (no LocalRuntime).
 *   - the bottom composer stays jvchat's `MessageInput` (attachments + tools
 *     menu), wired to `onSend` — so all existing chrome is preserved.
 *   - per-message **Debug** link after every completed answer opens the legacy
 *     `MessageDebugDialog` (reads the final-chunk debugData off message metadata).
 *   - **edit** uses assistant-ui's native ActionBar.Edit + edit composer, routed
 *     back to jvchat via `editAndResend`.
 *   - **branch** navigation is rendered in the assistant-ui message footer but
 *     drives jvchat's `selectBranchVersion`, because the external store keeps
 *     branch state on jvchat's side (snapshots), not in the runtime repository.
 */
import { createContext, useContext, useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessage,
  useMessageAttachment,
  type AppendMessage,
  type ReasoningMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Pencil,
  Wrench,
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
import { MessageInput } from "../MessageInput";
import { WelcomeScreen } from "../WelcomeScreen";
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

// --- markdown ---------------------------------------------------------------

/** Drop react-markdown's `node` prop so it isn't spread onto DOM elements. */
function omitNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const clone = { ...props };
  delete (clone as { node?: unknown }).node;
  return clone;
}

const MD_COMPONENTS: Components = {
  blockquote: (props) => (
    <blockquote
      className="border-l-2 border-zinc-300 dark:border-zinc-600 pl-3 my-2 text-zinc-600 dark:text-zinc-400"
      {...omitNode(props)}
    />
  ),
  a: (props) => (
    <a
      className="underline text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100"
      target="_blank"
      rel="noreferrer noopener"
      {...omitNode(props)}
    />
  ),
  ul: (props) => (
    <ul className="list-disc pl-4 sm:pl-6 my-2" {...omitNode(props)} />
  ),
  ol: (props) => (
    <ol className="list-decimal pl-4 sm:pl-6 my-2" {...omitNode(props)} />
  ),
  h1: (props) => (
    <h1 className="text-lg sm:text-xl font-bold my-2" {...omitNode(props)} />
  ),
  h2: (props) => (
    <h2 className="text-base sm:text-lg font-bold my-2" {...omitNode(props)} />
  ),
  h3: (props) => (
    <h3
      className="text-sm sm:text-base font-semibold my-2"
      {...omitNode(props)}
    />
  ),
  code: (props) => (
    <code
      className="rounded bg-zinc-200 dark:bg-zinc-800 px-1 py-0.5 text-[0.85em] font-mono"
      {...omitNode(props)}
    />
  ),
  pre: (props) => (
    <pre
      className="my-2 block overflow-x-auto rounded-lg border border-zinc-200 bg-zinc-100 p-3 text-xs sm:text-sm font-mono dark:border-white/10 dark:bg-zinc-900"
      {...omitNode(props)}
    />
  ),
};

const TextPart: TextMessagePartComponent = ({ text }) => (
  <div className="prose prose-sm dark:prose-invert max-w-none break-words text-sm leading-relaxed text-zinc-900 dark:text-zinc-50">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
      {text}
    </ReactMarkdown>
  </div>
);

// --- reasoning + tool parts -------------------------------------------------

const ReasoningPart: ReasoningMessagePartComponent = ({ text }) => {
  if (!text?.trim()) return null;
  return (
    <div className="my-1 border-l-2 border-zinc-200 dark:border-white/10 pl-3">
      <div className="whitespace-pre-wrap break-words text-xs font-mono leading-[1.65] text-zinc-500 dark:text-zinc-400">
        {text}
      </div>
    </div>
  );
};

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
}) => {
  const [open, setOpen] = useState(false);
  const argStr = args && Object.keys(args).length ? JSON.stringify(args) : "";
  const resStr = formatToolResult(result);
  return (
    <div className="my-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors",
          isError
            ? "border-red-300 text-red-600 dark:border-red-800 dark:text-red-400"
            : "border-zinc-200 text-zinc-500 hover:text-zinc-700 dark:border-white/10 dark:text-zinc-400 dark:hover:text-zinc-200",
        )}
      >
        <Wrench className="size-3 shrink-0 opacity-70" aria-hidden />
        <span className="font-mono">{toolName}</span>
        {argStr ? (
          <span className="truncate max-w-[16rem] opacity-60">{argStr}</span>
        ) : null}
      </button>
      {open && resStr ? (
        <pre className="mt-1 max-h-48 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-2 text-[11px] font-mono whitespace-pre-wrap break-words text-zinc-600 dark:border-white/10 dark:bg-black/30 dark:text-zinc-300">
          {resStr}
        </pre>
      ) : null}
    </div>
  );
};

// --- footer controls --------------------------------------------------------

/** Branch prev/next picker that drives jvchat's selectBranchVersion. */
function BranchPicker({ rootId }: { rootId?: string }) {
  const { branchSnapshots, branchVersionIndex, onBranchVersionChange } =
    useJvThread();
  if (!rootId) return null;
  const snaps = branchSnapshots[rootId];
  const count = snaps?.length ?? 0;
  if (count < 2) return null;
  const index = branchVersionIndex[rootId] ?? count - 1;
  return (
    <div className="inline-flex items-center gap-0.5 text-xs text-zinc-500 dark:text-zinc-400">
      <button
        type="button"
        aria-label="Previous version"
        disabled={index <= 0}
        onClick={() => onBranchVersionChange?.(rootId, index - 1)}
        className="rounded p-0.5 transition-colors hover:text-zinc-700 disabled:opacity-30 dark:hover:text-zinc-200"
      >
        <ChevronLeft className="size-4" aria-hidden />
      </button>
      <span className="tabular-nums">
        {index + 1}/{count}
      </span>
      <button
        type="button"
        aria-label="Next version"
        disabled={index >= count - 1}
        onClick={() => onBranchVersionChange?.(rootId, index + 1)}
        className="rounded p-0.5 transition-colors hover:text-zinc-700 disabled:opacity-30 dark:hover:text-zinc-200"
      >
        <ChevronRight className="size-4" aria-hidden />
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
      className="text-xs text-zinc-400 underline-offset-2 transition-colors hover:text-zinc-600 hover:underline dark:text-zinc-500 dark:hover:text-zinc-300"
    >
      Debug
    </button>
  );
}

function CopyAction() {
  return (
    <ActionBarPrimitive.Copy
      className="inline-flex size-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
      aria-label="Copy"
    >
      <MessagePrimitive.If copied>
        <Check className="size-4" aria-hidden />
      </MessagePrimitive.If>
      <MessagePrimitive.If copied={false}>
        <Copy className="size-4 stroke-[1.5px]" aria-hidden />
      </MessagePrimitive.If>
    </ActionBarPrimitive.Copy>
  );
}

// --- message renderers ------------------------------------------------------

const AssistantMessage = () => {
  const meta = useMessage(
    (m) => m.metadata?.custom as unknown as JvAssistantMeta | undefined,
  );
  return (
    <MessagePrimitive.Root className="group/message mx-auto w-full max-w-3xl px-2 py-3">
      <div className="flex flex-col gap-1">
        <MessagePrimitive.Parts
          components={{
            Text: TextPart,
            Reasoning: ReasoningPart,
            tools: { Fallback: ToolPart },
          }}
        />
        <div className="mt-1 flex items-center gap-2 opacity-0 transition-opacity group-hover/message:opacity-100 focus-within:opacity-100">
          <CopyAction />
          <BranchPicker rootId={meta?.branchRootId} />
          <DebugButton />
        </div>
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
        className="mb-2 max-h-64 w-auto max-w-[min(100%,20rem)] rounded-2xl border border-white/10 bg-black/20 object-contain"
      />
    );
  }
  return (
    <span className="mb-2 inline-flex items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-100 px-2 py-1.5 text-xs text-zinc-700 dark:border-white/10 dark:bg-zinc-800 dark:text-zinc-300">
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
    <MessagePrimitive.Root className="group/message mx-auto flex w-full max-w-3xl flex-col items-end px-2 py-3">
      <MessagePrimitive.Attachments
        components={{ Image: UserAttachment, File: UserAttachment }}
      />
      <div className="max-w-[85%] rounded-2xl bg-zinc-100 px-4 py-2.5 text-sm leading-relaxed text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50">
        <MessagePrimitive.Parts components={{ Text: TextPart }} />
      </div>
      <div className="mt-1 flex items-center gap-2 opacity-0 transition-opacity group-hover/message:opacity-100 focus-within:opacity-100">
        <BranchPicker rootId={meta?.branchRootId} />
        <ActionBarPrimitive.Edit
          className="inline-flex size-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          aria-label="Edit"
        >
          <Pencil className="size-4 stroke-[1.5px]" aria-hidden />
        </ActionBarPrimitive.Edit>
      </div>
    </MessagePrimitive.Root>
  );
};

const UserEditComposer = () => (
  <MessagePrimitive.Root className="mx-auto flex w-full max-w-3xl flex-col items-end px-2 py-3">
    <ComposerPrimitive.Root className="w-full max-w-[85%] rounded-2xl border border-zinc-200 bg-white p-2 dark:border-white/10 dark:bg-zinc-900">
      <ComposerPrimitive.Input
        autoFocus
        className="w-full resize-none bg-transparent px-2 py-1 text-sm text-zinc-900 outline-none dark:text-zinc-50"
      />
      <div className="mt-1 flex justify-end gap-1.5">
        <ComposerPrimitive.Cancel
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-zinc-500 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800"
          aria-label="Cancel edit"
        >
          <X className="size-3.5" aria-hidden /> Cancel
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send
          className="inline-flex items-center gap-1 rounded-md bg-zinc-900 px-2.5 py-1 text-xs text-white transition-colors hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
          aria-label="Save edit"
        >
          <Check className="size-3.5" aria-hidden /> Save
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  </MessagePrimitive.Root>
);

// --- text extraction for adapter callbacks ----------------------------------

function appendMessageText(message: AppendMessage): string {
  return message.content
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("")
    .trim();
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
    onNew: async (message: AppendMessage) => {
      const text = appendMessageText(message);
      if (text) onSend(text);
    },
    onEdit: async (message: AppendMessage) => {
      const text = appendMessageText(message);
      const editedId = message.sourceId;
      if (text && editedId) await onEditMessage?.(editedId, text);
    },
    onCancel: async () => {
      onStop?.();
    },
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
        <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col bg-background">
          <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
            <ThreadPrimitive.Empty>
              <WelcomeScreen
                agentName={welcomeAgentName}
                agentAvatar={welcomeAgentAvatar}
                description={welcomeDescription}
              />
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages
              components={{
                UserMessage,
                AssistantMessage,
                UserEditComposer,
              }}
            />
            <div className="h-4" />
          </ThreadPrimitive.Viewport>

          <div className="flex-shrink-0 px-2 pb-3 sm:px-4">
            <div className="mx-auto w-full max-w-3xl">
              <MessageInput
                onSend={onSend}
                disabled={composerDisabled}
                placeholder={placeholder}
                composerMenu={composerMenu}
                onStop={onStop}
              />
            </div>
          </div>
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
