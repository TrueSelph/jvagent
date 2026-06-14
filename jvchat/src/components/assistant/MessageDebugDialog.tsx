/**
 * Per-message Debug dialog, extracted verbatim from the legacy Thread so the
 * assistant-ui thread keeps jvchat's "Debug" view after every completed message.
 * Shows the message content (parsed if JSON) and the full `type=final` SSE
 * payload (`interaction{…}`, `report[]`, …), with a Copy JSON action.
 */
import { useMemo } from "react";

import { useTheme } from "../../context/ThemeContext";
import { cn } from "../../lib/utils";
import type { Message } from "../../types/message";
import { tryParseJsonDisplay } from "../../utils/tryParseJsonDisplay";
import { JsonViewer } from "../JsonViewer";

export function MessageDebugDialog({
  message,
  onClose,
}: {
  message: Message | null;
  onClose: () => void;
}) {
  const { theme } = useTheme();
  const jsonPanelDark = theme === "dark";

  const payload = useMemo(() => {
    if (!message) return { parsed: null as unknown | null, raw: "" };
    const raw =
      message.debugData?.interaction?.response ?? message.content ?? "";
    const s = typeof raw === "string" ? raw : String(raw);
    return { parsed: tryParseJsonDisplay(s), raw: s };
  }, [message]);

  if (!message) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-white/10 max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 sm:px-6 py-3 border-b border-zinc-200 dark:border-white/10 flex items-center justify-between flex-shrink-0">
          <h3 className="text-base sm:text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Debug View - Message {message.id.substring(0, 20)}...
          </h3>
          <button
            onClick={onClose}
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
            {payload.parsed != null ? (
              <JsonViewer
                data={payload.parsed}
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
                  {payload.raw}
                </pre>
              </div>
            )}
          </div>
          {message.debugData ? (
            <div>
              <h4 className="text-xs sm:text-sm font-semibold text-zinc-600 dark:text-zinc-300 mb-2">
                Full JSON Response (type=final):
              </h4>
              <JsonViewer
                data={message.debugData}
                defaultExpandDepth={2}
                dark={jsonPanelDark}
                maxHeight="60vh"
              />
            </div>
          ) : (
            <div className="text-xs sm:text-sm text-zinc-400 dark:text-zinc-500 italic">
              Debug data not available yet. Waiting for final interaction data...
            </div>
          )}
        </div>
        <div className="px-4 sm:px-6 py-3 border-t border-zinc-200 dark:border-white/10 flex justify-end flex-shrink-0">
          <button
            onClick={() => {
              navigator.clipboard.writeText(
                JSON.stringify(message.debugData, null, 2),
              );
            }}
            className="px-4 py-2 bg-zinc-900 text-white rounded-lg hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 touch-manipulation text-sm sm:text-base transition-colors duration-150"
          >
            Copy JSON
          </button>
        </div>
      </div>
    </div>
  );
}
