/**
 * assistant-ui base Thread (shadcn styling + tailwindcss-animate), adapted for
 * the popup messenger. Adds: an extended welcome (avatar + greeting + quick-reply
 * cards), an optional notice banner, an optional consent gate, agent-driven
 * suggestion chips, masked reasoning, and voice/upload controls. Message/thread
 * structure follows the upstream assistant-ui base theme.
 */

import { useEffect, useState, type FC } from "react";
import {
  ActionBarPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useComposerRuntime,
  type FileMessagePartComponent,
  type ImageMessagePartComponent,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  CopyIcon,
  InfoIcon,
  PaperclipIcon,
  RefreshCwIcon,
  SquareIcon,
} from "lucide-react";
import type { MessengerConfig } from "../../shared/config";
import type { UploadedAttachment } from "../streaming/uploadClient";
import type { MessageAction } from "../streaming/types";
import { acceptConsent, hasAcceptedConsent } from "../streaming/session";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { ChatServicesProvider, useChatServices } from "./context";
import { MicButton } from "./MicButton";
import { SpeakButton } from "./SpeakButton";
import { AttachmentButton, AttachmentChips } from "./AttachmentBar";

export interface ThreadServices {
  config: MessengerConfig;
  sendText: (text: string) => void;
  getToken: () => string | undefined;
  attachments: UploadedAttachment[];
  addAttachment: (a: UploadedAttachment) => void;
  removeAttachment: (url: string) => void;
  suggestions: MessageAction[];
}

export function Thread(props: ThreadServices) {
  const {
    config,
    sendText,
    getToken,
    attachments,
    addAttachment,
    removeAttachment,
    suggestions,
  } = props;

  const [accepted, setAccepted] = useState(
    () => !config.consent || hasAcceptedConsent(config.agentId, config.consent)
  );

  return (
    <ChatServicesProvider value={{ config, getToken }}>
      <ThreadPrimitive.Root
        className="aui-root box-border flex h-full flex-col overflow-hidden bg-background"
        style={{ ["--thread-max-width" as string]: "100%" }}
      >
        {!accepted && config.consent ? (
          <ConsentGate
            config={config}
            onAccept={() => {
              acceptConsent(config.agentId, config.consent!);
              setAccepted(true);
            }}
          />
        ) : (
          <ThreadPrimitive.Viewport className="relative flex flex-1 flex-col overflow-y-scroll scroll-smooth px-4 pt-4">
            {config.notice && <NoticeBanner text={config.notice} />}

            <ThreadWelcome config={config} onPick={sendText} />

            <ThreadPrimitive.Messages
              components={{ UserMessage, AssistantMessage }}
            />

            <ThreadPrimitive.If empty={false}>
              <div className="min-h-4 flex-grow" />
            </ThreadPrimitive.If>

            <div className="msgr-composer-dock sticky bottom-0 mt-3 flex w-full flex-col items-stretch gap-2 pb-3">
              <ThreadScrollToBottom />
              {suggestions.length > 0 && (
                <Suggestions items={suggestions} onPick={sendText} />
              )}
              <Composer
                config={config}
                sendText={sendText}
                attachments={attachments}
                addAttachment={addAttachment}
                removeAttachment={removeAttachment}
              />
            </div>
          </ThreadPrimitive.Viewport>
        )}
      </ThreadPrimitive.Root>
    </ChatServicesProvider>
  );
}

const NoticeBanner: FC<{ text: string }> = ({ text }) => (
  <div className="border-border bg-muted/60 text-muted-foreground animate-in fade-in mb-3 flex items-start gap-2 rounded-xl border px-3 py-2 text-xs leading-relaxed">
    <InfoIcon className="mt-0.5 size-3.5 flex-none" />
    <span>{text}</span>
  </div>
);

const ConsentGate: FC<{ config: MessengerConfig; onAccept: () => void }> = ({
  config,
  onAccept,
}) => {
  const [declined, setDeclined] = useState(false);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      {config.avatar && (
        <img
          src={config.avatar}
          alt=""
          className="border-border size-12 rounded-full border object-cover"
        />
      )}
      <p className="text-foreground text-sm leading-relaxed whitespace-pre-wrap">
        {config.consent}
      </p>
      {declined ? (
        <p className="text-muted-foreground text-sm">
          You can close this window. Reopen it to continue.
        </p>
      ) : (
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setDeclined(true)}>
            Decline
          </Button>
          <Button onClick={onAccept}>Accept</Button>
        </div>
      )}
    </div>
  );
};

const Suggestions: FC<{
  items: MessageAction[];
  onPick: (value: string) => void;
}> = ({ items, onPick }) => (
  <div className="flex flex-wrap justify-end gap-1.5">
    {items.map((s) => (
      <button
        key={`${s.label}:${s.value}`}
        type="button"
        onClick={() => onPick(s.value)}
        className="border-border text-foreground hover:bg-muted animate-in fade-in slide-in-from-bottom-1 rounded-full border bg-background px-3 py-1.5 text-xs transition-colors"
      >
        {s.label}
      </button>
    ))}
  </div>
);

const ThreadScrollToBottom: FC = () => (
  <ThreadPrimitive.ScrollToBottom asChild>
    <TooltipIconButton
      tooltip="Scroll to bottom"
      variant="outline"
      className="absolute -top-10 size-8 self-center rounded-full p-2 disabled:invisible"
    >
      <ArrowDownIcon />
    </TooltipIconButton>
  </ThreadPrimitive.ScrollToBottom>
);

const ThreadWelcome: FC<{
  config: MessengerConfig;
  onPick: (text: string) => void;
}> = ({ config, onPick }) => (
  <ThreadPrimitive.Empty>
    <div className="flex w-full flex-grow flex-col">
      <div className="flex w-full flex-grow flex-col items-center justify-center gap-3 px-2 py-6">
        {config.avatar && (
          <img
            src={config.avatar}
            alt=""
            className="border-border animate-in fade-in zoom-in-95 size-14 rounded-full border object-cover"
          />
        )}
        <p className="animate-in fade-in slide-in-from-bottom-2 text-foreground text-xl font-semibold">
          {config.title}
        </p>
        {config.greeting && (
          <p className="animate-in fade-in slide-in-from-bottom-2 text-muted-foreground max-w-[90%] text-center text-sm">
            {config.greeting}
          </p>
        )}
      </div>
      {config.quickReplies.length > 0 && (
        <div className="mt-1 grid w-full gap-2">
          {config.quickReplies.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => onPick(r)}
              className="border-border hover:bg-muted animate-in fade-in slide-in-from-bottom-1 flex w-full items-center justify-between rounded-2xl border bg-background px-4 py-3 text-start text-sm font-medium shadow-sm transition-colors"
            >
              <span>{r}</span>
              <ArrowUpIcon className="text-muted-foreground size-3.5 rotate-45" />
            </button>
          ))}
        </div>
      )}
    </div>
  </ThreadPrimitive.Empty>
);

const Composer: FC<{
  config: MessengerConfig;
  sendText: (text: string) => void;
  attachments: UploadedAttachment[];
  addAttachment: (a: UploadedAttachment) => void;
  removeAttachment: (url: string) => void;
}> = ({ config, sendText, attachments, addAttachment, removeAttachment }) => {
  const composer = useComposerRuntime();
  // Mirror the composer text so the send button can enable on typed text OR a
  // pending attachment (an image may be sent with no accompanying text).
  const [text, setText] = useState(() => composer.getState().text);
  useEffect(
    () => composer.subscribe(() => setText(composer.getState().text)),
    [composer]
  );

  const hasAttachments = attachments.length > 0;
  const canSend = text.trim().length > 0 || hasAttachments;
  const submit = () => {
    const value = composer.getState().text;
    if (!value.trim() && !hasAttachments) return;
    composer.setText("");
    sendText(value);
  };

  return (
    <ComposerPrimitive.Root className="border-border bg-background flex w-full flex-col rounded-2xl border px-2 pt-1.5 pb-1.5">
      {hasAttachments && (
        <div className="px-1 pb-1.5">
          <AttachmentChips attachments={attachments} onRemove={removeAttachment} />
        </div>
      )}
      <ComposerPrimitive.Input
        rows={1}
        autoFocus
        // Own the Enter path (submit on Enter, newline on Shift+Enter) so it also
        // covers attachment-only turns; avoids a double-submit with the primitive.
        submitMode="none"
        placeholder="Send a message..."
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        className="placeholder:text-muted-foreground max-h-32 min-h-8 w-full resize-none bg-transparent px-1.5 py-1 text-sm outline-none"
      />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-0.5">
          {config.attachments && <AttachmentButton onUploaded={addAttachment} />}
          {config.voice && <MicButton />}
        </div>
        <ThreadPrimitive.If running={false}>
          <Button
            size="icon"
            className="size-8 rounded-full"
            aria-label="Send message"
            disabled={!canSend}
            onClick={submit}
          >
            <ArrowUpIcon className="size-4" />
          </Button>
        </ThreadPrimitive.If>
        <ThreadPrimitive.If running>
          <ComposerPrimitive.Cancel asChild>
            <Button
              size="icon"
              className="size-8 rounded-full"
              aria-label="Stop generating"
            >
              <SquareIcon className="size-3 fill-current" />
            </Button>
          </ComposerPrimitive.Cancel>
        </ThreadPrimitive.If>
      </div>
    </ComposerPrimitive.Root>
  );
};

const ReasoningPart: FC<{ text: string }> = ({ text }) => (
  <div className="border-muted-foreground/30 text-muted-foreground my-2 border-s-2 ps-3 text-xs whitespace-pre-wrap">
    {text}
  </div>
);

// Uploaded image → thumbnail (click to open full size in a new tab).
const UserImagePart: ImageMessagePartComponent = ({ image }) => (
  <a
    href={image}
    target="_blank"
    rel="noreferrer"
    className="mb-1 block overflow-hidden rounded-xl border border-border/40"
  >
    <img
      src={image}
      alt="attachment"
      className="max-h-48 max-w-full object-cover"
    />
  </a>
);

// Uploaded non-image file → a compact chip with its name.
const UserFilePart: FileMessagePartComponent = ({ filename }) => (
  <span className="bg-background/50 text-foreground mb-1 flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs">
    <PaperclipIcon className="size-3.5 shrink-0" />
    <span className="truncate">{filename || "Attachment"}</span>
  </span>
);

const UserMessage: FC = () => (
  <MessagePrimitive.Root className="animate-in fade-in slide-in-from-bottom-1 flex w-full flex-col items-end py-2">
    <div className="msgr-user-bubble bg-muted text-foreground flex max-w-[85%] flex-col rounded-xl px-4 py-2 text-sm break-words">
      <MessagePrimitive.Parts
        components={{ Image: UserImagePart, File: UserFilePart }}
      />
    </div>
  </MessagePrimitive.Root>
);

const AssistantMessage: FC = () => {
  const { config } = useChatServices();
  return (
    <MessagePrimitive.Root className="animate-in fade-in slide-in-from-bottom-1 relative flex w-full flex-col py-2">
      <div className="text-foreground max-w-full text-sm leading-7 break-words">
        <MessagePrimitive.Parts
          components={{ Text: MarkdownText, Reasoning: ReasoningPart }}
        />
      </div>
      <div className="text-muted-foreground mt-1 flex min-h-8 items-center gap-0.5">
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          className="flex items-center gap-0.5"
        >
          <ActionBarPrimitive.Copy asChild>
            <TooltipIconButton tooltip="Copy">
              <MessagePrimitive.If copied>
                <CheckIcon />
              </MessagePrimitive.If>
              <MessagePrimitive.If copied={false}>
                <CopyIcon />
              </MessagePrimitive.If>
            </TooltipIconButton>
          </ActionBarPrimitive.Copy>
          <ActionBarPrimitive.Reload asChild>
            <TooltipIconButton tooltip="Regenerate">
              <RefreshCwIcon />
            </TooltipIconButton>
          </ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
        {config.voice && <SpeakButton />}
      </div>
    </MessagePrimitive.Root>
  );
};
