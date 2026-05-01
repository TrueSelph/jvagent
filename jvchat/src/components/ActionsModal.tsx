import { useState, useEffect, useCallback } from "react";
import { apiClient } from "../config/api";
import { useTheme } from "../context/ThemeContext";
import { JsonCodeEditor } from "./JsonCodeEditor";

interface ActionItem {
  id: string;
  entity?: string;
  context?: {
    agent_id?: string;
    enabled?: boolean;
    namespace?: string;
    label?: string;
    description?: string;
    metadata?: Record<string, unknown>;
  };
  properties?: Record<string, unknown>;
  [key: string]: unknown;
}

interface ActionsModalProps {
  agentId: string;
  onClose: () => void;
  isEmbedded?: boolean;
}

export function ActionsModal({
  agentId,
  onClose,
  isEmbedded = true,
}: ActionsModalProps) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAction, setSelectedAction] = useState<ActionItem | null>(null);

  // Editable state
  const [editedEnabled, setEditedEnabled] = useState(true);
  const [editedContextJson, setEditedContextJson] = useState("");
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [idCopied, setIdCopied] = useState(false);

  console.log("editedEnabled", editedContextJson);

  const fetchActions = useCallback(async (): Promise<ActionItem[]> => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getActions(agentId, {
        page: 1,
        per_page: 100,
        enabled_only: false,
      });
      const list = res?.actions ?? res?.data?.actions ?? res ?? [];
      const arr = Array.isArray(list) ? list : [];
      setActions(arr);
      return arr;
    } catch (err: any) {
      console.error("Failed to fetch actions:", err);
      setError(err.message || "Failed to load actions");
      setActions([]);
      return [];
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    let cancelled = false;
    fetchActions().then((list) => {
      if (cancelled) return;
      if (list.length > 0) {
        setSelectedAction((prev) => {
          const stillExists = prev && list.some((a) => a.id === prev.id);
          return stillExists ? prev : list[0];
        });
      } else {
        setSelectedAction(null);
      }
    });
    return () => { cancelled = true; };
  }, [fetchActions]);

  // Sync editable state when selected action changes
  useEffect(() => {
    if (!selectedAction) {
      setEditedEnabled(true);
      setEditedContextJson("");
      setUpdateError(null);
      return;
    }
    const ctx = selectedAction.context ?? {};
    console.log("ctx", ctx);
    setEditedEnabled(ctx.enabled ?? true);
    setEditedContextJson(
      Object.keys(ctx).length > 0 ? JSON.stringify(ctx, null, 2) : "{}"
    );
    setUpdateError(null);
  }, [selectedAction]);

  useEffect(() => {
    setIdCopied(false);
  }, [selectedAction?.id]);

  const copyActionId = async () => {
    if (!selectedAction) return;
    const id = selectedAction.id;
    try {
      await navigator.clipboard.writeText(id);
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = id;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        return;
      }
    }
    setIdCopied(true);
    window.setTimeout(() => setIdCopied(false), 2000);
  };

  useEffect(() => {
    if (!isEmbedded || !onClose) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [isEmbedded, onClose]);

  const getActionLabel = (a: ActionItem) => {
    const label = a.context?.label ?? a.context?.metadata?.name ?? a.entity;
    return String(label ?? a.id).slice(0, 50);
  };

  const hasChanges = (): boolean => {
    if (!selectedAction) return false;
    const ctx = selectedAction.context ?? {};
    if ((ctx.enabled ?? true) !== editedEnabled) return true;
    try {
      if (!editedContextJson.trim()) return false;
      const parsed = JSON.parse(editedContextJson);
      if (typeof parsed !== "object" || parsed === null) return false;
      return JSON.stringify(ctx) !== JSON.stringify(parsed);
    } catch {
      return false;
    }
  };

  const buildUpdatePayload = (): {
    description?: string;
    enabled?: boolean;
    properties?: Record<string, unknown>;
  } => {
    if (!selectedAction) return {};
    const ctx = selectedAction.context ?? {};
    const payload: {
      description?: string;
      enabled?: boolean;
      properties?: Record<string, unknown>;
    } = {};
    try {
      if (!editedContextJson.trim()) return payload;
      const parsed = JSON.parse(editedContextJson);
      if (typeof parsed !== "object" || parsed === null) return payload;
      if ((ctx.enabled ?? true) !== editedEnabled) {
        payload.enabled = editedEnabled;
      }
      if (String(ctx.description ?? "") !== String(parsed.description ?? "")) {
        payload.description = parsed.description as string;
      }
      const propsKeys = Object.keys(parsed).filter(
        (k) => !["enabled", "description", "agent_id"].includes(k)
      );
      if (propsKeys.length > 0) {
        const changed: Record<string, unknown> = {};
        const ctxRec = ctx as Record<string, unknown>
        for (const k of propsKeys) {
          if (JSON.stringify(parsed[k]) !== JSON.stringify(ctxRec[k])) {
            changed[k] = parsed[k];
          }
        }
        if (Object.keys(changed).length > 0) {
          payload.properties = changed;
        }
      }
    } catch {
      return payload;
    }
    return payload;
  };

  const handleUpdate = async () => {
    if (!selectedAction) return;
    const payload = buildUpdatePayload();
    if (Object.keys(payload).length === 0) return;
    setUpdating(true);
    setUpdateError(null);
    try {
      await apiClient.updateAction(selectedAction.id, payload);
      await apiClient.reloadAction(selectedAction.id);
      const freshList = await fetchActions();
      const updated = freshList.find((a) => a.id === selectedAction.id);
      if (updated) setSelectedAction(updated);
    } catch (err: any) {
      console.error("Update failed:", err);
      setUpdateError(err.message || "Update failed");
    } finally {
      setUpdating(false);
    }
  };

  const content = (
    <div
      className={`rounded-lg shadow-xl w-full max-w-[95vw] max-h-[95vh] h-[min(88vh,920px)] flex flex-col border ${dark ? "bg-zinc-900 border-zinc-700 text-zinc-100" : "bg-white border-zinc-200"}`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      <div
        className={`flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${dark ? "border-zinc-700" : "border-zinc-200"}`}
      >
        <h2
          className={`text-xl sm:text-2xl font-semibold ${dark ? "text-zinc-100" : "text-zinc-900"}`}
        >
          Agent Actions
        </h2>
        <button
          onClick={onClose}
          className={`p-2 rounded-lg transition-colors ${dark ? "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700" : "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100"}`}
          aria-label="Close"
        >
          <svg
            className="w-5 h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left: Actions list */}
        <div
          className={`flex-shrink-0 w-64 sm:w-72 border-r overflow-y-auto ${dark ? "border-zinc-700 bg-zinc-800/50" : "border-zinc-200 bg-zinc-50"}`}
        >
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div
                className={`animate-spin rounded-full h-8 w-8 border-b-2 ${dark ? "border-zinc-400" : "border-zinc-600"}`}
              />
            </div>
          ) : error ? (
            <p className={`px-4 py-4 text-sm ${dark ? "text-red-400" : "text-red-600"}`}>
              {error}
            </p>
          ) : actions.length === 0 ? (
            <p
              className={`px-4 py-4 text-sm ${dark ? "text-zinc-400" : "text-zinc-500"}`}
            >
              No actions found.
            </p>
          ) : (
            <div className="py-2">
              {actions.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setSelectedAction(a)}
                  className={`w-full text-left px-4 py-2.5 text-sm truncate transition-colors ${selectedAction?.id === a.id ? (dark ? "bg-zinc-600/50 text-zinc-100" : "bg-zinc-100 text-zinc-900") : dark ? "hover:bg-zinc-700/80 text-zinc-200" : "hover:bg-zinc-200 text-zinc-800"}`}
                >
                  {getActionLabel(a)}
                  {a.context?.enabled === false && (
                    <span className="ml-1 text-xs opacity-75">(disabled)</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Right: Action details */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden p-4 sm:p-6">
          {!selectedAction ? (
            <p
              className={`text-sm ${dark ? "text-zinc-400" : "text-zinc-500"}`}
            >
              Select an action to view and edit its properties.
            </p>
          ) : (
            <div className="space-y-4 w-full flex-1 flex flex-col min-h-0 overflow-hidden">
              <div className="flex items-center gap-4 flex-shrink-0">
                <h3
                  className={`text-sm font-medium ${dark ? "text-zinc-300" : "text-zinc-600"}`}
                >
                  {getActionLabel(selectedAction)} ({selectedAction.entity ?? "—"})
                </h3>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editedEnabled}
                      onChange={(e) => setEditedEnabled(e.target.checked)}
                      className={`rounded ${dark ? "border-zinc-600 text-zinc-500" : "border-zinc-300 text-zinc-600"}`}
                    />
                    <span
                      className={`text-sm ${dark ? "text-zinc-200" : "text-zinc-800"}`}
                    >
                      Enabled
                    </span>
                  </label>
                  <button
                    type="button"
                    onClick={copyActionId}
                    title={idCopied ? "Copied" : "Copy action ID"}
                    aria-label={idCopied ? "Action ID copied" : "Copy action ID"}
                    className={`p-1.5 rounded-md transition-colors ${dark ? "text-zinc-400 hover:text-zinc-300 hover:bg-zinc-700" : "text-zinc-500 hover:text-zinc-600 hover:bg-zinc-100"}`}
                  >
                    {idCopied ? (
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                    ) : (
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                        />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              <div className="flex-1 min-h-0 flex flex-col">
                <label
                  className={`block text-sm font-medium mb-2 flex-shrink-0 ${dark ? "text-zinc-300" : "text-zinc-600"}`}
                >
                  Context (JSON)
                </label>
                <JsonCodeEditor
                  value={editedContextJson}
                  onChange={setEditedContextJson}
                  placeholder="{}"
                  dark={dark}
                  fillHeight
                  className="flex-1 min-h-0"
                />
                <p
                  className={`mt-1 text-xs flex-shrink-0 ${dark ? "text-zinc-400" : "text-zinc-500"}`}
                >
                  Edit context. Update sends only changed fields. Invalid JSON
                  will be ignored.
                </p>
              </div>

              {updateError && (
                <p className="text-sm text-red-600 dark:text-red-400">
                  {updateError}
                </p>
              )}

              <div className="flex justify-end">
                <button
                  onClick={handleUpdate}
                  disabled={!hasChanges() || updating}
                  className="px-4 py-2 bg-zinc-600 text-white text-sm font-medium rounded-lg hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {updating ? "Updating..." : "Update only changed properties"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  if (isEmbedded) {
    return (
      <div
        className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${dark ? "bg-black/70" : "bg-black/50"}`}
        onClick={(e) => {
          if (e.target === e.currentTarget && onClose) onClose();
        }}
      >
        {content}
      </div>
    );
  }

  return content;
}
