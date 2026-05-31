/**
 * jvchat chat surface — assistant-ui's official Thread component (vendored from
 * the assistant-ui open-source project under src/components/assistant-ui),
 * driven by jvchat's data plane through an ExternalStore runtime over
 * `useStreaming` (SSE, sessions, branch snapshots, debugData).
 *
 * The runtime owns: messages (built into assistant-ui ThreadMessageLike via
 * buildThreadMessages), send, edit, cancel, and attachments (a custom
 * AttachmentAdapter hands the original File objects back to jvchat's send
 * pipeline). The per-message Debug link is injected into the official action
 * bar via DebugContext.
 */
import { useMemo, useState } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";

import type { SendMessageOptions } from "../../hooks/useStreaming";
import { buildThreadMessages } from "../../lib/threadMessages";
import type { Message } from "../../types/message";
import { Thread } from "../assistant-ui/thread";
import {
  DebugContext,
  ComposerMenuContext,
} from "../assistant-ui/debug-action";
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

// --- main component ---------------------------------------------------------

export function AssistantThread({
  messages,
  isStreaming = false,
  onEditMessage,
  onSend,
  onStop,
  composerDisabled = false,
  composerMenu,
}: AssistantThreadProps) {
  const [debugMessage, setDebugMessage] = useState<Message | null>(null);

  const threadMessages = useMemo(
    () => buildThreadMessages(messages, isStreaming),
    [messages, isStreaming],
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

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <DebugContext.Provider value={{ openDebug: setDebugMessage }}>
        <ComposerMenuContext.Provider value={composerMenu}>
          <Thread />
          <MessageDebugDialog
            message={debugMessage}
            onClose={() => setDebugMessage(null)}
          />
        </ComposerMenuContext.Provider>
      </DebugContext.Provider>
    </AssistantRuntimeProvider>
  );
}

export default AssistantThread;
