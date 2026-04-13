import { useState, useEffect, useCallback } from "react";
import { apiClient } from "../config/api";
import { useTheme } from "../context/ThemeContext";

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
    setEditedEnabled(ctx.enabled ?? true);
    setEditedContextJson(
      Object.keys(ctx).length > 0 ? JSON.stringify(ctx, null, 2) : "{}"
    );
    setUpdateError(null);
  }, [selectedAction]);

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
      className={`rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col border ${dark ? "bg-slate-900 border-slate-700 text-slate-100" : "bg-white border-gray-200"}`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      <div
        className={`flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${dark ? "border-slate-700" : "border-gray-200"}`}
      >
        <h2
          className={`text-xl sm:text-2xl font-semibold ${dark ? "text-slate-100" : "text-gray-900"}`}
        >
          Agent Actions
        </h2>
        <button
          onClick={onClose}
          className={`p-2 rounded-lg transition-colors ${dark ? "text-slate-400 hover:text-slate-100 hover:bg-slate-700" : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"}`}
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
          className={`flex-shrink-0 w-64 sm:w-72 border-r overflow-y-auto ${dark ? "border-slate-700 bg-slate-800/50" : "border-gray-200 bg-gray-50"}`}
        >
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div
                className={`animate-spin rounded-full h-8 w-8 border-b-2 ${dark ? "border-indigo-400" : "border-indigo-600"}`}
              />
            </div>
          ) : error ? (
            <p className={`px-4 py-4 text-sm ${dark ? "text-red-400" : "text-red-600"}`}>
              {error}
            </p>
          ) : actions.length === 0 ? (
            <p
              className={`px-4 py-4 text-sm ${dark ? "text-slate-400" : "text-gray-500"}`}
            >
              No actions found.
            </p>
          ) : (
            <div className="py-2">
              {actions.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setSelectedAction(a)}
                  className={`w-full text-left px-4 py-2.5 text-sm truncate transition-colors ${selectedAction?.id === a.id ? (dark ? "bg-indigo-600/50 text-indigo-100" : "bg-indigo-100 text-indigo-900") : dark ? "hover:bg-slate-700/80 text-slate-200" : "hover:bg-gray-200 text-gray-800"}`}
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
              className={`text-sm ${dark ? "text-slate-400" : "text-gray-500"}`}
            >
              Select an action to view and edit its properties.
            </p>
          ) : (
            <div className="space-y-4 w-full flex-1 flex flex-col min-h-0 overflow-hidden">
              <div className="flex items-center gap-4 flex-shrink-0">
                <h3
                  className={`text-sm font-medium ${dark ? "text-slate-300" : "text-gray-600"}`}
                >
                  {getActionLabel(selectedAction)} ({selectedAction.entity ?? "—"})
                </h3>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editedEnabled}
                    onChange={(e) => setEditedEnabled(e.target.checked)}
                    className={`rounded ${dark ? "border-slate-600 text-indigo-500" : "border-gray-300 text-indigo-600"}`}
                  />
                  <span
                    className={`text-sm ${dark ? "text-slate-200" : "text-gray-800"}`}
                  >
                    Enabled
                  </span>
                </label>
              </div>

              <div className="flex-1 min-h-0 flex flex-col">
                <label
                  className={`block text-sm font-medium mb-2 flex-shrink-0 ${dark ? "text-slate-300" : "text-gray-600"}`}
                >
                  Context (JSON)
                </label>
                <textarea
                  value={editedContextJson}
                  onChange={(e) => setEditedContextJson(e.target.value)}
                  placeholder='{}'
                  className={`flex-1 min-h-0 w-full px-3 py-2 border rounded-lg text-sm font-mono resize-none ${dark ? "bg-slate-800 border-slate-600 text-slate-100" : "bg-white border-gray-300 text-gray-900"}`}
                  style={{ minHeight: "320px" }}
                />
                <p
                  className={`mt-1 text-xs flex-shrink-0 ${dark ? "text-slate-400" : "text-gray-500"}`}
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
                  className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
