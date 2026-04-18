import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types/message";
import React from "react";
import { resolveMediaUrl } from "../utils/mediaUrl";

function metaString(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return String(v);
  return "";
}

/** Avoid duplicate <img> when markdown already embeds the same URL as metadata.media_url. */
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

function MetadataImage({
  src,
  role,
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
      <p
        className={`text-sm my-2 italic ${
          role === "user"
            ? "text-slate-300 dark:text-indigo-200/80"
            : "text-slate-600 dark:text-slate-400"
        }`}
      >
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
        className={`underline block my-2 ${
          message.role === "user"
            ? "text-slate-200 hover:text-white dark:text-indigo-200"
            : "text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
        }`}
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

interface MessageListProps {
  messages: Message[];
  thoughtMessages?: Message[];
  showThinking?: boolean;
  thinkingText?: string;
}

export function MessageList({
  messages,
  thoughtMessages = [],
  showThinking = false,
  thinkingText = "Thinking...",
}: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevLenRef = useRef<number>(0);
  const [debugMessage, setDebugMessage] = useState<Message | null>(null);
  const totalVisibleMessages = messages.length + thoughtMessages.length;

  useEffect(() => {
    // Avoid smooth-scroll on every token append (prevents visible flicker).
    const behavior = totalVisibleMessages > prevLenRef.current ? "smooth" : "auto";
    messagesEndRef.current?.scrollIntoView({ behavior });
    prevLenRef.current = totalVisibleMessages;
  }, [messages, thoughtMessages, totalVisibleMessages]);

  if (messages.length === 0 && thoughtMessages.length === 0) {
    return null;
  }

  return (
    <>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-4 py-4 sm:py-6 space-y-3 sm:space-y-4">
        {thoughtMessages.length > 0 && (
          <div className="rounded-xl border border-amber-300/40 dark:border-amber-500/30 bg-amber-50/70 dark:bg-amber-900/10 p-3 sm:p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs sm:text-sm font-semibold text-amber-800 dark:text-amber-300">
                Thinking & Tooling Stream
              </h3>
              <span className="text-[11px] sm:text-xs text-amber-700/80 dark:text-amber-400/80">
                separate from chat transcript
              </span>
            </div>
            <div className="space-y-2">
              {thoughtMessages.map((message) => (
                <div
                  key={message.id}
                  className="rounded-lg bg-white/70 dark:bg-slate-800/70 border border-amber-200/60 dark:border-slate-700 px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-[11px] sm:text-xs font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400">
                      {message.thoughtType || "thought"}
                    </span>
                    <span className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400">
                      {new Date(message.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="text-xs sm:text-sm text-slate-800 dark:text-slate-200 whitespace-pre-wrap break-words">
                    {message.content}
                    {message.streaming && (
                      <span className="inline-block w-0.5 sm:w-1 h-3 sm:h-4 ml-0.5 sm:ml-1 bg-current animate-pulse align-middle" />
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${
              message.role === "user" ? "justify-end" : "justify-start"
            } animate-fade-in`}
          >
            <div
              className={`max-w-[85%] sm:max-w-3xl rounded-2xl px-3 sm:px-4 py-2 sm:py-3 relative shadow-sm ${
                message.role === "user"
                  ? "bg-slate-600 text-white dark:bg-indigo-900/40 dark:text-indigo-200"
                  : "bg-gray-200 text-gray-900 dark:bg-slate-800 dark:text-slate-100 dark:border dark:border-slate-700"
              }`}
            >
              <div className="break-words text-sm sm:text-base">
                <MessageMediaBlock message={message} />
                {message.content?.trim() ? (
                <div className="markdown-content">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      // Style code blocks
                      code: ({
                        inline,
                        className,
                        children,
                        ...props
                      }: any) => {
                        return !inline ? (
                          <pre
                            className={`overflow-x-auto rounded p-2 sm:p-3 my-2 ${
                              message.role === "user"
                                ? "bg-slate-700/70 text-slate-100 dark:bg-indigo-900/60 dark:text-indigo-100"
                                : "bg-slate-800 text-slate-100 dark:bg-slate-900 dark:text-slate-200"
                            }`}
                            {...props}
                          >
                            <code className={className} {...props}>
                              {children}
                            </code>
                          </pre>
                        ) : (
                          <code
                            className={`px-1 py-0.5 rounded ${
                              message.role === "user"
                                ? "bg-slate-700/70 text-slate-100 dark:bg-indigo-900/60 dark:text-indigo-100"
                                : "bg-slate-200 text-slate-800 dark:bg-slate-600 dark:text-slate-200"
                            }`}
                            {...props}
                          >
                            {children}
                          </code>
                        );
                      },
                      // Style blockquotes
                      blockquote: ({ children, ...props }: any) => (
                        <blockquote
                          className={`border-l-4 pl-3 sm:pl-4 my-2 ${
                            message.role === "user"
                              ? "border-slate-400 text-slate-200 dark:border-indigo-500/60 dark:text-indigo-200"
                              : "border-slate-400 text-slate-700 dark:border-slate-500 dark:text-slate-300"
                          }`}
                          {...props}
                        >
                          {children}
                        </blockquote>
                      ),
                      // Style links
                      a: ({ children, ...props }: any) => (
                        <a
                          className={`underline ${
                            message.role === "user"
                              ? "text-slate-300 hover:text-white dark:text-indigo-200 dark:hover:text-indigo-100"
                              : "text-indigo-600 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300"
                          }`}
                          {...props}
                        >
                          {children}
                        </a>
                      ),
                      // Style lists
                      ul: ({ children, ...props }: any) => (
                        <ul className="list-disc pl-4 sm:pl-6 my-2" {...props}>
                          {children}
                        </ul>
                      ),
                      ol: ({ children, ...props }: any) => (
                        <ol
                          className="list-decimal pl-4 sm:pl-6 my-2"
                          {...props}
                        >
                          {children}
                        </ol>
                      ),
                      // Style headings
                      h1: ({ children, ...props }: any) => (
                        <h1
                          className="text-lg sm:text-xl font-bold my-2"
                          {...props}
                        >
                          {children}
                        </h1>
                      ),
                      h2: ({ children, ...props }: any) => (
                        <h2
                          className="text-base sm:text-lg font-bold my-2"
                          {...props}
                        >
                          {children}
                        </h2>
                      ),
                      h3: ({ children, ...props }: any) => (
                        <h3
                          className="text-sm sm:text-base font-semibold my-2"
                          {...props}
                        >
                          {children}
                        </h3>
                      ),
                      // Style paragraphs - add cursor to last paragraph when streaming
                      p: ({ children, ...props }: any) => {
                        // Extract text content from children for comparison
                        const extractText = (n: any): string => {
                          if (typeof n === "string") return n;
                          if (typeof n === "number") return String(n);
                          if (
                            React.isValidElement(n) &&
                            (n.props as any)?.children
                          ) {
                            return extractText((n.props as any).children);
                          }
                          if (Array.isArray(n)) {
                            return n.map(extractText).join("");
                          }
                          return "";
                        };
                        const childrenText = extractText(children);
                        // Check if this is the last paragraph by seeing if content ends with this paragraph's text
                        const isLastParagraph =
                          message.streaming &&
                          childrenText.trim() &&
                          message.content.trim().endsWith(childrenText.trim());
                        return (
                          <p className="my-1 sm:my-2" {...props}>
                            {children}
                            {isLastParagraph && (
                              <span className="inline-block w-0.5 sm:w-1 h-3 sm:h-4 ml-0.5 sm:ml-1 bg-current animate-pulse align-middle" />
                            )}
                          </p>
                        );
                      },
                      // Style tables
                      table: ({ children, ...props }: any) => (
                        <div className="overflow-x-auto my-2">
                          <table className="border-collapse border" {...props}>
                            {children}
                          </table>
                        </div>
                      ),
                      th: ({ children, ...props }: any) => (
                        <th
                          className={`border px-2 sm:px-4 py-1 sm:py-2 ${
                            message.role === "user"
                              ? "bg-slate-700/70 border-slate-500 dark:bg-indigo-900/60 dark:border-indigo-500/60"
                              : "bg-slate-200 border-slate-400 dark:bg-slate-700 dark:border-slate-600"
                          }`}
                          {...props}
                        >
                          {children}
                        </th>
                      ),
                      td: ({ children, ...props }: any) => (
                        <td
                          className={`border px-2 sm:px-4 py-1 sm:py-2 ${
                            message.role === "user"
                              ? "border-slate-500 dark:border-indigo-500/60"
                              : "border-slate-400 dark:border-slate-600"
                          }`}
                          {...props}
                        >
                          {children}
                        </td>
                      ),
                    }}
                  >
                    {message.content}
                  </ReactMarkdown>
                </div>
                ) : null}
              </div>
              <div className="flex items-center justify-between mt-1 sm:mt-2 gap-2">
                <div
                  className={`text-xs ${
                    message.role === "user"
                      ? "text-slate-300 dark:text-indigo-200"
                      : "text-slate-500 dark:text-slate-400"
                  }`}
                >
                  {new Date(message.timestamp).toLocaleTimeString()}
                </div>
                {(() => {
                  // Show debug button if:
                  // 1. Message has debugData, OR
                  // 2. Message is the last assistant message for its interactionId
                  const shouldShowDebug =
                    message.debugData ||
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
                      })());

                  return shouldShowDebug ? (
                    <button
                      onClick={() => {
                        // Find the message with debugData for this interaction
                        const debugMessageForInteraction = messages.find(
                          (m) =>
                            m.interactionId === message.interactionId &&
                            m.debugData,
                        );
                        setDebugMessage(debugMessageForInteraction || message);
                      }}
                      className={`text-xs px-2 py-1 rounded touch-manipulation ${
                        message.role === "user"
                          ? "bg-slate-700 hover:bg-slate-600 text-white dark:bg-indigo-900/60 dark:hover:bg-indigo-800/60 dark:text-indigo-100"
                          : "bg-slate-200 hover:bg-slate-300 text-slate-700 dark:bg-slate-600 dark:hover:bg-slate-500 dark:text-slate-200"
                      }`}
                    >
                      Debug
                    </button>
                  ) : null;
                })()}
              </div>
            </div>
          </div>
        ))}

        {showThinking && (
          <div className="flex justify-start animate-fade-in">
            <div className="max-w-[85%] sm:max-w-3xl rounded-2xl px-4 sm:px-5 py-3 sm:py-4 bg-gray-200 text-gray-900 dark:bg-slate-800 dark:text-slate-100 dark:border dark:border-slate-700 shadow-sm">
              <div className="flex items-center gap-3">
                <div className="flex gap-1.5 items-center">
                  <span className="w-2 h-2 rounded-full bg-slate-500 animate-bounce [animation-delay:0ms]" />
                  <span className="w-2 h-2 rounded-full bg-slate-500 animate-bounce [animation-delay:150ms]" />
                  <span className="w-2 h-2 rounded-full bg-slate-500 animate-bounce [animation-delay:300ms]" />
                </div>
                <span className="text-xs sm:text-sm text-slate-600 dark:text-slate-300">
                  {thinkingText}
                </span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Debug Modal */}
      {debugMessage && (
        <div
          className="fixed inset-0 bg-black/50 dark:bg-black/70 flex items-center justify-center z-50 p-2 sm:p-4"
          onClick={() => setDebugMessage(null)}
        >
          <div
            className="bg-white dark:bg-gray-800 rounded-lg max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-shrink-0">
              <h3 className="text-base sm:text-lg font-semibold text-gray-900 dark:text-gray-100">
                Debug View - Message {debugMessage.id.substring(0, 20)}...
              </h3>
              <button
                onClick={() => setDebugMessage(null)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-2xl touch-manipulation p-1"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 sm:p-6">
              <div className="mb-4">
                <h4 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                  Message Content:
                </h4>
                <div className="bg-gray-50 dark:bg-gray-900 p-2 sm:p-3 rounded border border-gray-200 dark:border-gray-600">
                  <pre className="whitespace-pre-wrap text-xs sm:text-sm text-gray-800 dark:text-gray-200">
                    {debugMessage.debugData?.interaction?.response ||
                      debugMessage.content}
                  </pre>
                </div>
              </div>
              {debugMessage.debugData ? (
                <div>
                  <h4 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                    Full JSON Response (type=final):
                  </h4>
                  <div className="bg-gray-900 p-2 sm:p-4 rounded border border-gray-700 overflow-x-auto">
                    <pre className="text-xs text-green-400">
                      {JSON.stringify(debugMessage.debugData, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 italic">
                  Debug data not available yet. Waiting for final interaction
                  data...
                </div>
              )}
            </div>
            <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-gray-200 dark:border-gray-700 flex justify-end flex-shrink-0">
              <button
                onClick={() => {
                  navigator.clipboard.writeText(
                    JSON.stringify(debugMessage.debugData, null, 2),
                  );
                }}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600 touch-manipulation text-sm sm:text-base"
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
