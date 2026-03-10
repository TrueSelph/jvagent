import {
  useState,
  useEffect,
  useRef,
  useLayoutEffect,
  useCallback,
} from "react";
import { apiClient } from "../config/api";
import { getSelectedAgent } from "../utils/storage";
import { useTheme } from "../context/ThemeContext";

interface DebugInteractionsProps {
  onClose?: () => void;
  isEmbedded?: boolean;
}

export function DebugInteractions({
  onClose,
  isEmbedded = false,
}: DebugInteractionsProps) {
  const [parentInteractions, setParentInteractions] = useState<any[]>([]);
  const [modelAction, setModelAction] = useState<any>(null);
  const [pagination, setPagination] = useState<{
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  } | null>(null);
  const [targetAgentId, setTargetAgentId] = useState<string | null>(null);
  const [selectedInteraction, setSelectedInteraction] = useState<any>(null);
  const [selectedParentIndex, setSelectedParentIndex] = useState<number | null>(
    null,
  );
  const [selectedMetricIndex, setSelectedMetricIndex] = useState<number | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [darkMode, setDarkMode] = useState(false);
  const { theme: appTheme } = useTheme();
  const [historyText, setHistoryText] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [improveInstruction, setImproveInstruction] = useState("");
  const [improveModel, setImproveModel] = useState("gpt-4o");
  const [improving, setImproving] = useState(false);
  const [improveResult, setImproveResult] = useState("");

  const userRef = useRef<HTMLTextAreaElement>(null);
  const systemRef = useRef<HTMLTextAreaElement>(null);
  const historyRef = useRef<HTMLTextAreaElement>(null);
  const improveInstructionRef = useRef<HTMLTextAreaElement>(null);
  const improveResultRef = useRef<HTMLTextAreaElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const lastFocusedInteractionId = useRef<string | null>(null);

  const adjustHeight = (el: HTMLTextAreaElement | null) => {
    if (!el) return;

    // Find the scrolling parent correctly
    const scrollParent = scrollContainerRef.current;
    const scrollPos = scrollParent ? scrollParent.scrollTop : window.scrollY;

    // Temporarily disable transitions to avoid jumping if any exist
    const transition = el.style.transition;
    el.style.transition = "none";

    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;

    el.style.transition = transition;

    // Restore scroll position immediately to prevent jumping
    if (scrollParent) {
      scrollParent.scrollTop = scrollPos;
    } else {
      window.scrollTo(0, scrollPos);
    }
  };

  const preserveScroll = (fn: () => void) => {
    if (typeof window === "undefined") {
      fn();
      return;
    }
    const y = window.scrollY || window.pageYOffset || 0;
    fn();
    setTimeout(() => window.scrollTo({ top: y, behavior: "auto" }), 0);
  };

  const selectInteraction = useCallback(
    (parentIdx: number, metricIdx: number, parentsData: any[]) => {
      preserveScroll(() => {
        setSelectedParentIndex(parentIdx);
        setSelectedMetricIndex(metricIdx);

        const parent = parentsData[parentIdx];
        if (!parent) return;

        const metric = parent.metrics[metricIdx];
        if (!metric) return;

        const pd = metric.data || {};
        // Get history from metric data or parent's conversation history
        const history = pd.history || parent.conversationHistory || [];

        setSelectedInteraction({
          id: metric.id,
          data: {
            user_prompt: pd.user_prompt || pd.prompt || "",
            system_prompt: pd.system_prompt || "",
            response: pd.response || "",
            model: pd.model || "",
            history: history,
          },
        });
        setTestResult(null);
      });
    },
    [],
  );

  const loadMore = useCallback(async () => {
    if (
      !targetAgentId ||
      !pagination ||
      pagination.page >= pagination.total_pages
    )
      return;
    setLoadingMore(true);
    setError(null);
    try {
      const nextPage = pagination.page + 1;
      const logsResponse = await apiClient.getLogs({
        category: "INTERACTION",
        agent_id: targetAgentId,
        page: nextPage,
        page_size: 50,
      });
      setPagination(logsResponse.pagination);
      const newParents = (logsResponse.logs || [])
        .map((log: any) => {
          const interactionData = log.log_data?.interaction_data || {};
          const metrics = interactionData.observability_metrics || [];
          const utterance = interactionData.utterance;
          const conversationHistory =
            interactionData.conversation_history || [];
          return { id: log.log_id, utterance, metrics, conversationHistory };
        })
        .filter((p: any) => p.metrics && p.metrics.length > 0);
      setParentInteractions((prev) => [...prev, ...newParents]);
    } catch (err: any) {
      setError(err.message || "Failed to load more logs");
    } finally {
      setLoadingMore(false);
    }
  }, [targetAgentId, pagination]);

  const initializeDebugSession = useCallback(async () => {
    setLoading(true);
    setError(null);

    let agentsData;
    try {
      agentsData = await apiClient.getAgents();
    } catch (error: any) {
      if (error.response?.status === 401 || error.message?.includes("401")) {
        setError("Session expired. Please log in again.");
      } else {
        setError(error.message || "Authentication failed");
      }
      setLoading(false);
      return;
    }

    try {
      if (!agentsData || !agentsData.agents) throw new Error("No agents found");

      const selectedAgentName = getSelectedAgent();
      const targetAgent = selectedAgentName
        ? agentsData.agents.find(
            (a: any) => a.context?.name === selectedAgentName,
          )
        : agentsData.agents[0];

      if (!targetAgent) throw new Error("No agents found");

      setTargetAgentId(targetAgent.id);

      const actionsData = await apiClient.getActions(targetAgent.id);
      const actions = actionsData.actions || [];
      const modelActionItem = actions.find(
        (a: any) => a.context?.label === "openai_lm",
      );
      setModelAction(modelActionItem || null);

      const logsResponse = await apiClient.getLogs({
        category: "INTERACTION",
        agent_id: targetAgent.id,
        page: 1,
        page_size: 200,
      });

      setPagination(logsResponse.pagination);

      const parents = (logsResponse.logs || [])
        .map((log: any) => {
          const interactionData = log.log_data?.interaction_data || {};
          const metrics = interactionData.observability_metrics || [];
          const utterance = interactionData.utterance;
          const conversationHistory =
            interactionData.conversation_history || [];
          return { id: log.log_id, utterance, metrics, conversationHistory };
        })
        .filter((p: any) => p.metrics && p.metrics.length > 0);

      setParentInteractions(parents);

      if (parents.length > 0) {
        const latestParentIdx = 0;
        const latestMetricIdx = parents[latestParentIdx].metrics.length - 1;
        selectInteraction(latestParentIdx, latestMetricIdx, parents);
      }
    } catch (err: any) {
      console.error(err);

      if (err.code === "ERR_NETWORK" || err.message === "Network Error") {
        setError(
          "Cannot connect to server. Please check if the jvagent server is running and CORS is enabled.",
        );
      } else {
        setError(err.message || "Failed to load interaction logs");
      }
    } finally {
      setLoading(false);
    }
  }, [selectInteraction]);

  const hasMorePages = pagination && pagination.page < pagination.total_pages;

  useEffect(() => {
    initializeDebugSession();
  }, [initializeDebugSession]);

  useEffect(() => {
    const historyData = selectedInteraction?.data?.history;
    const hasHistory = Array.isArray(historyData);

    setHistoryText(
      hasHistory && historyData.length > 0
        ? JSON.stringify(historyData, null, 2)
        : "",
    );
    setShowHistory(true); // Always show history section so users can see/edit it

    if (
      selectedInteraction?.id &&
      selectedInteraction.id !== lastFocusedInteractionId.current
    ) {
      lastFocusedInteractionId.current = selectedInteraction.id;
      setTimeout(() => userRef.current?.focus?.(), 0);
    } else if (!selectedInteraction) {
      lastFocusedInteractionId.current = null;
    }
  }, [selectedInteraction]);

  useLayoutEffect(() => {
    adjustHeight(userRef.current);
  }, [selectedInteraction?.data.user_prompt, loading]);

  useLayoutEffect(() => {
    adjustHeight(systemRef.current);
  }, [selectedInteraction?.data.system_prompt, loading]);

  useLayoutEffect(() => {
    if (showHistory) {
      adjustHeight(historyRef.current);
    }
  }, [historyText, showHistory, loading]);

  useLayoutEffect(() => {
    adjustHeight(improveInstructionRef.current);
  }, [improveInstruction, loading]);

  useLayoutEffect(() => {
    adjustHeight(improveResultRef.current);
  }, [improveResult, loading]);

  const handleTest = async () => {
    if (!selectedInteraction || !modelAction) return;

    preserveScroll(() => {
      setTesting(true);
      setTestResult(null);
    });

    try {
      const payload = {
        prompt: selectedInteraction.data.user_prompt,
        system: selectedInteraction.data.system_prompt,
        model: selectedInteraction.data.model,
        history: selectedInteraction.data.history || [],
      };

      const data = await apiClient.queryAction(modelAction.id, payload);
      preserveScroll(() =>
        setTestResult({
          success: true,
          response: data.response,
          data: data,
        }),
      );
    } catch (error: any) {
      preserveScroll(() =>
        setTestResult({
          success: false,
          error: error.message,
        }),
      );
    } finally {
      setTesting(false);
    }
  };

  const handleImprovePrompt = async () => {
    if (!selectedInteraction || !modelAction || !improveInstruction) return;

    preserveScroll(() => {
      setImproving(true);
      setImproveResult("");
    });

    try {
      const improvePayload = {
        prompt: `Given the following context, improve the prompts based on the instruction.

User Prompt:
${selectedInteraction.data.user_prompt}

System Prompt:
${selectedInteraction.data.system_prompt}

Conversation History:
${JSON.stringify(selectedInteraction.data.history || [], null, 2)}

RESULT:
${selectedInteraction.data.response}

Improvement Instruction:
${improveInstruction}

Provide improvement instruction on how to improve the prompt. Return a raw markdown.`,
        system:
          "You are a prompt engineering expert. Analyze the given prompts and improve them based on the instruction.",
        model: improveModel,
        history: [],
      };

      const data = await apiClient.queryAction(modelAction.id, improvePayload);
      preserveScroll(() => setImproveResult(data.response || ""));
    } catch (error: any) {
      preserveScroll(() => setImproveResult(`Error: ${error.message}`));
    } finally {
      setImproving(false);
    }
  };

  const handleExport = () => {
    if (parentInteractions.length === 0) return;

    const dataToExport = {
      parentInteractions: parentInteractions,
      pagination: pagination,
      selectedParentIndex: selectedParentIndex,
      selectedMetricIndex: selectedMetricIndex,
      metadata: {
        exportedAt: new Date().toISOString(),
        agentId: targetAgentId,
      },
    };

    const blob = new Blob([JSON.stringify(dataToExport, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `interactions_${new Date().getTime()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const content = event.target?.result as string;
        const parsed = JSON.parse(content);

        // Check if it's the new format (full list) or legacy format (single interaction)
        if (
          parsed.parentInteractions &&
          Array.isArray(parsed.parentInteractions)
        ) {
          preserveScroll(() => {
            setParentInteractions(parsed.parentInteractions);
            setPagination(parsed.pagination || null);

            const pIdx =
              typeof parsed.selectedParentIndex === "number"
                ? parsed.selectedParentIndex
                : 0;
            const mIdx =
              typeof parsed.selectedMetricIndex === "number"
                ? parsed.selectedMetricIndex
                : 0;

            if (parsed.parentInteractions.length > 0) {
              selectInteraction(pIdx, mIdx, parsed.parentInteractions);
            }
          });
        } else {
          // Legacy format or single interaction export
          const interactionData = parsed.interaction || parsed;
          const testResultData = parsed.testResult || null;

          if (interactionData?.data) {
            preserveScroll(() => {
              setSelectedParentIndex(null);
              setSelectedMetricIndex(null);
              setSelectedInteraction(interactionData);
              setTestResult(testResultData);
            });
          } else {
            setError("Invalid import file format");
          }
        }
      } catch (err) {
        console.error("Import failed", err);
        setError("Failed to parse import file");
      }
      e.target.value = "";
    };
    reader.readAsText(file);
  };

  const truncate = (str: string, length = 100) => {
    if (!str) return "";
    return str.length > length ? str.substring(0, length) + "..." : str;
  };

  const effectiveDarkMode = isEmbedded ? appTheme === "dark" : darkMode;

  useEffect(() => {
    if (!isEmbedded || !onClose) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [isEmbedded, onClose]);

  const headerButtons = (
    <div className="flex items-center gap-2">
      {!isEmbedded && (
        <button
          onClick={() => setDarkMode(!effectiveDarkMode)}
          className={`px-3 py-1.5 rounded text-sm ${effectiveDarkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${effectiveDarkMode ? "border-gray-600" : "border-gray-300"}`}
        >
          {effectiveDarkMode ? "☀️ Light" : "🌙 Dark"}
        </button>
      )}
      <button
        onClick={initializeDebugSession}
        disabled={loading}
        className={
          isEmbedded
            ? `px-3 py-2 text-sm rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2 ${effectiveDarkMode ? "text-slate-300 bg-slate-700 hover:bg-slate-600" : "text-gray-700 bg-gray-100 hover:bg-gray-200"}`
            : `px-3 py-1.5 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed ${effectiveDarkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${effectiveDarkMode ? "border-gray-600" : "border-gray-300"}`
        }
        title="Refresh"
      >
        {isEmbedded ? (
          <>
            <svg
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            <span className="hidden sm:inline">Refresh</span>
          </>
        ) : (
          "🔄 Refresh"
        )}
      </button>
      <button
        onClick={handleExport}
        disabled={!selectedInteraction}
        className={
          isEmbedded
            ? `px-3 py-2 text-sm rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${effectiveDarkMode ? "text-slate-300 bg-slate-700 hover:bg-slate-600" : "text-gray-700 bg-gray-100 hover:bg-gray-200"}`
            : `px-3 py-1.5 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed ${effectiveDarkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${effectiveDarkMode ? "border-gray-600" : "border-gray-300"}`
        }
        title="Export"
      >
        {isEmbedded ? "Export" : "📤 Export"}
      </button>
      <label
        className={
          isEmbedded
            ? `px-3 py-2 text-sm rounded-lg cursor-pointer transition-colors ${effectiveDarkMode ? "text-slate-300 bg-slate-700 hover:bg-slate-600" : "text-gray-700 bg-gray-100 hover:bg-gray-200"}`
            : `px-3 py-1.5 rounded text-sm cursor-pointer ${effectiveDarkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${effectiveDarkMode ? "border-gray-600" : "border-gray-300"}`
        }
      >
        {isEmbedded ? "Import" : "📥 Import"}
        <input
          type="file"
          accept=".json"
          className="hidden"
          onChange={handleImport}
        />
      </label>
      {isEmbedded && onClose && (
        <button
          onClick={onClose}
          className={`p-2 rounded-lg transition-colors ${effectiveDarkMode ? "text-slate-400 hover:text-slate-100 hover:bg-slate-700" : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"}`}
          title="Close"
          aria-label="Close debug interactions"
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
      )}
    </div>
  );

  const content = (
    <div
      className={`${isEmbedded ? `rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col ${effectiveDarkMode ? "bg-slate-900 text-slate-100" : "bg-white"}` : `min-h-screen p-6 ${effectiveDarkMode ? "bg-gray-900 text-gray-100" : "bg-gray-50 text-gray-900"}`}`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      {/* Header - matches GraphViewer when embedded */}
      <div
        className={
          isEmbedded
            ? `flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${effectiveDarkMode ? "border-slate-700" : "border-gray-200"}`
            : "flex justify-between items-center mb-6"
        }
      >
        <h2
          className={
            isEmbedded
              ? `text-xl sm:text-2xl font-semibold ${effectiveDarkMode ? "text-slate-100" : "text-gray-900"}`
              : "text-2xl font-bold"
          }
        >
          {isEmbedded ? "Debug Interactions" : "Debug Interactions"}
        </h2>
        {headerButtons}
      </div>

      <div
        ref={!isEmbedded ? scrollContainerRef : undefined}
        className={
          isEmbedded ? "flex-1 overflow-hidden relative" : "max-w-full mx-auto"
        }
      >
        {isEmbedded && loading && (
          <div className={`absolute inset-0 flex items-center justify-center ${effectiveDarkMode ? "bg-slate-900" : "bg-white"}`}>
            <div className="text-center">
              <div className={`animate-spin rounded-full h-12 w-12 border-b-2 mx-auto ${effectiveDarkMode ? "border-indigo-400" : "border-indigo-600"}`} />
              <p className={`mt-4 ${effectiveDarkMode ? "text-slate-400" : "text-gray-600"}`}>Loading interactions...</p>
            </div>
          </div>
        )}

        {isEmbedded && error && !loading && parentInteractions.length === 0 && (
          <div className={`absolute inset-0 flex items-center justify-center p-4 ${effectiveDarkMode ? "bg-slate-900" : "bg-white"}`}>
            <div className={`rounded-lg p-6 max-w-md w-full ${effectiveDarkMode ? "bg-red-900/30 border border-red-800" : "bg-red-50 border border-red-200"}`}>
              <h3 className={`text-lg font-semibold mb-2 ${effectiveDarkMode ? "text-red-300" : "text-red-800"}`}>
                Error Loading Interactions
              </h3>
              <p className={`mb-4 ${effectiveDarkMode ? "text-red-300" : "text-red-700"}`}>{error}</p>
              <button
                onClick={() => {
                  setError(null);
                  initializeDebugSession();
                }}
                className={`w-full px-4 py-2 text-white rounded-lg transition-colors ${effectiveDarkMode ? "bg-red-600 hover:bg-red-500" : "bg-red-600 hover:bg-red-700"}`}
              >
                Retry
              </button>
            </div>
          </div>
        )}

        <div
          ref={isEmbedded ? scrollContainerRef : undefined}
          className={
            isEmbedded &&
            (loading || (error && parentInteractions.length === 0))
              ? "hidden"
              : isEmbedded
                ? "h-full overflow-y-auto p-4 sm:p-6"
                : ""
          }
        >
          {/* Error Display - inline for transient errors or when not embedded */}
          {error && (
            <div className={`border p-4 rounded-lg mb-4 flex justify-between items-center ${effectiveDarkMode ? "bg-red-900/30 border-red-800 text-red-300" : "bg-red-50 border-red-200 text-red-700"}`}>
              <span>{error}</span>
              <button
                onClick={() => setError(null)}
                className={`font-bold ${effectiveDarkMode ? "text-red-300 hover:text-red-100" : "text-red-700 hover:text-red-900"}`}
              >
                ×
              </button>
            </div>
          )}

          {/* Interaction Selector */}
          {!loading && parentInteractions.length > 0 && (
            <div
              className={`rounded-lg shadow-sm p-4 mb-6 border ${effectiveDarkMode ? "bg-slate-800 border-slate-700" : "bg-white border-gray-200"}`}
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                    Interaction
                  </label>
                  <select
                    className={`w-full p-2 border rounded text-sm ${effectiveDarkMode ? "bg-slate-700 border-slate-600 text-slate-100" : "bg-white border-gray-300"}`}
                    value={selectedParentIndex ?? ""}
                    onChange={(e) =>
                      selectInteraction(
                        parseInt(e.target.value),
                        0,
                        parentInteractions,
                      )
                    }
                  >
                    {parentInteractions.map((p, idx) => (
                      <option key={p.id || idx} value={idx}>
                        [{idx + 1}]{" "}
                        {truncate(p.utterance || "(no utterance)", 100)}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                    Response
                  </label>
                  <select
                    className={`w-full p-2 border rounded text-sm ${effectiveDarkMode ? "bg-slate-700 border-slate-600 text-slate-100" : "bg-white border-gray-300"}`}
                    value={selectedMetricIndex ?? ""}
                    onChange={(e) =>
                      selectInteraction(
                        selectedParentIndex!,
                        parseInt(e.target.value),
                        parentInteractions,
                      )
                    }
                    disabled={selectedParentIndex === null}
                  >
                    {selectedParentIndex !== null &&
                      parentInteractions[selectedParentIndex]?.metrics.map(
                        (m: any, mi: number) => (
                          <option key={mi} value={mi}>
                            [{mi + 1}]{" "}
                            {truncate(
                              m.data?.response ||
                                m.data?.model ||
                                "Interaction",
                              100,
                            )}
                          </option>
                        ),
                      )}
                  </select>
                </div>
              </div>
              {hasMorePages && (
                <div className="mt-4 flex justify-center">
                  <button
                    onClick={loadMore}
                    disabled={loadingMore}
                    className={`px-3 py-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${isEmbedded ? (effectiveDarkMode ? "text-slate-300 bg-slate-700 rounded-lg hover:bg-slate-600" : "text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200") : `rounded ${effectiveDarkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"} border ${effectiveDarkMode ? "border-gray-600" : "border-gray-300"}`}`}
                  >
                    {loadingMore ? "Loading..." : "Load more"}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Loading State - non-embedded only; embedded uses overlay */}
          {!isEmbedded && loading && (
            <div
              className={`${effectiveDarkMode ? "bg-gray-800" : "bg-white"} rounded-lg shadow-sm p-12 text-center border ${effectiveDarkMode ? "border-gray-700" : "border-gray-200"}`}
            >
              <div className="text-lg">Loading interactions...</div>
            </div>
          )}

          {/* Main Content */}
          {!loading && selectedInteraction && (
            <div
              className={`rounded-lg shadow-sm p-6 border ${effectiveDarkMode ? "bg-slate-800 border-slate-700" : "bg-white border-gray-200"}`}
            >
              <div className="space-y-6">
                {/* Original Response */}
                {selectedInteraction.data.response && (
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      Original Response
                    </label>
                    <div
                      className={`p-4 rounded text-sm border ${effectiveDarkMode ? "bg-gray-900 border-gray-700" : "bg-red-50 border-red-200"}`}
                    >
                      <pre className="whitespace-pre-wrap font-mono text-xs">
                        {selectedInteraction.data.response}
                      </pre>
                    </div>
                  </div>
                )}
                {/* User Prompt */}
                <div>
                  <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                    User Prompt
                  </label>
                  <textarea
                    ref={userRef}
                    value={selectedInteraction.data.user_prompt}
                    onChange={(e) => {
                      setSelectedInteraction({
                        ...selectedInteraction,
                        data: {
                          ...selectedInteraction.data,
                          user_prompt: e.target.value,
                        },
                      });
                    }}
                    className={`w-full p-3 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-800 border-slate-600 text-slate-100" : "bg-blue-50 border-blue-200 text-gray-900"}`}
                    style={{ overflow: "hidden" }}
                  />
                </div>
                {/* System Prompt */}
                <div>
                  <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                    System Prompt
                  </label>
                  <textarea
                    ref={systemRef}
                    value={selectedInteraction.data.system_prompt}
                    onChange={(e) => {
                      setSelectedInteraction({
                        ...selectedInteraction,
                        data: {
                          ...selectedInteraction.data,
                          system_prompt: e.target.value,
                        },
                      });
                    }}
                    className={`w-full p-3 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-800 border-slate-600 text-slate-100" : "bg-yellow-50 border-yellow-200 text-gray-900"}`}
                    style={{ overflow: "hidden" }}
                  />
                </div>
                {/* History - Only show if exists */}
                {showHistory && (
                  <div>
                    <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                      History (JSON)
                    </label>
                    <textarea
                      ref={historyRef}
                      value={historyText}
                      onChange={(e) => setHistoryText(e.target.value)}
                      onBlur={() => {
                        try {
                          const parsed = historyText
                            ? JSON.parse(historyText)
                            : [];
                          setSelectedInteraction((si: any) =>
                            si
                              ? { ...si, data: { ...si.data, history: parsed } }
                              : si,
                          );
                        } catch (err) {
                          console.error("Invalid JSON for history:", err);
                        }
                      }}
                      className={`w-full p-3 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-900 border-slate-700 text-slate-100" : "bg-gray-100 border-gray-200 text-gray-900"}`}
                      style={{ overflow: "hidden" }}
                    />
                  </div>
                )}
                {/* Model */}
                <div>
                  <label className={`block text-sm font-medium mb-2 ${effectiveDarkMode ? "text-slate-300" : ""}`}>
                    Model
                  </label>
                  <input
                    type="text"
                    value={selectedInteraction.data.model}
                    onChange={(e) =>
                      setSelectedInteraction({
                        ...selectedInteraction,
                        data: {
                          ...selectedInteraction.data,
                          model: e.target.value,
                        },
                      })
                    }
                    className={`w-full p-2 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-700 border-slate-600 text-slate-100" : "bg-white border-gray-300"}`}
                  />
                </div>
                {/* Test Button */}
                <div className="flex justify-end">
                  <button
                    onClick={handleTest}
                    disabled={testing || !modelAction}
                    className="px-6 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {testing ? "Testing..." : "🧪 Run Test"}
                  </button>
                </div>
                {/* Test Result */}
                {testResult && (
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      Test Result
                    </label>
                    <div
                      className={`p-4 rounded text-sm border ${testResult.success ? (effectiveDarkMode ? "bg-green-900 border-green-700" : "bg-green-50 border-green-200") : effectiveDarkMode ? "bg-red-900 border-red-700" : "bg-red-50 border-red-200"}`}
                    >
                      {testResult.success ? (
                        <pre
                          className={`whitespace-pre-wrap font-mono text-xs ${effectiveDarkMode ? "text-green-100" : "text-green-700"}`}
                        >
                          {testResult.response}
                        </pre>
                      ) : (
                        <div
                          className={
                            effectiveDarkMode ? "text-red-100" : "text-red-700"
                          }
                        >
                          <div className="font-medium mb-2">Error</div>
                          <div>{testResult.error}</div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {/* show Original Response below test result for quick comparison */}
                {selectedInteraction.data.response && (
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      Original Response
                    </label>
                    <div
                      className={`p-4 rounded text-sm border ${effectiveDarkMode ? "bg-gray-900 border-gray-700" : "bg-red-50 border-red-200"}`}
                    >
                      <pre className="whitespace-pre-wrap font-mono text-xs">
                        {selectedInteraction.data.response}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Improve Prompt Section */}
          {!loading && selectedInteraction && (
            <div
              className={`rounded-lg shadow-sm p-6 border mt-6 ${effectiveDarkMode ? "bg-slate-800 border-slate-700" : "bg-white border-gray-200"}`}
            >
              <h2 className={`text-xl font-semibold mb-4 ${effectiveDarkMode ? "text-slate-100" : ""}`}>Improve Prompt</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">
                    Improvement Instruction
                  </label>
                  <textarea
                    ref={improveInstructionRef}
                    value={improveInstruction}
                    onChange={(e) => setImproveInstruction(e.target.value)}
                    placeholder="Describe how you want to improve the prompts..."
                    className={`w-full p-3 border rounded text-sm ${effectiveDarkMode ? "bg-slate-700 border-slate-600 text-slate-100" : "bg-white border-gray-300"}`}
                    style={{ overflow: "hidden" }}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">
                    Model for Improvement
                  </label>
                  <input
                    type="text"
                    value={improveModel}
                    onChange={(e) => setImproveModel(e.target.value)}
                    className={`w-full p-2 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-700 border-slate-600 text-slate-100" : "bg-white border-gray-300"}`}
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => {
                      const promptToCopy = `Given the following context, improve the prompts based on the instruction.

User Prompt:
${selectedInteraction.data.user_prompt}

System Prompt:
${selectedInteraction.data.system_prompt}

Conversation History:
${JSON.stringify(selectedInteraction.data.history || [], null, 2)}

RESULT:
${selectedInteraction.data.response}

Improvement Instruction:
${improveInstruction}

Provide improvement instruction on how to improve the prompt. Return a raw markdown.`;
                      navigator.clipboard.writeText(promptToCopy);
                    }}
                    className="px-6 py-2 bg-gray-600 text-white rounded-lg font-medium hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    📋 Copy Prompt
                  </button>
                  <button
                    onClick={handleImprovePrompt}
                    disabled={improving || !modelAction || !improveInstruction}
                    className="px-6 py-2 bg-purple-600 text-white rounded-lg font-medium hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {improving ? "Improving..." : "✨ Improve Prompt"}
                  </button>
                </div>
                {improveResult && (
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      Improvement Result
                    </label>
                    <textarea
                      ref={improveResultRef}
                      value={improveResult}
                      onChange={(e) => setImproveResult(e.target.value)}
                      className={`w-full p-3 border rounded text-sm font-mono ${effectiveDarkMode ? "bg-slate-800 border-slate-600 text-slate-100" : "bg-purple-50 border-purple-200 text-gray-900"}`}
                      style={{ overflow: "hidden" }}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Empty State */}
          {!loading &&
            !selectedInteraction &&
            parentInteractions.length === 0 && (
              <div
                className={`${effectiveDarkMode ? "bg-gray-800 text-gray-400" : "bg-white text-gray-500"} rounded-lg shadow-sm p-12 text-center border ${effectiveDarkMode ? "border-gray-700" : "border-gray-200"}`}
              >
                No interaction logs available. Ensure database logging is
                enabled with INTERACTION level, then try refreshing.
              </div>
            )}
        </div>
      </div>
    </div>
  );

  if (isEmbedded) {
    return (
      <div
        className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${effectiveDarkMode ? "bg-black/70" : "bg-black/50"}`}
        onClick={(e) => {
          if (e.target === e.currentTarget && onClose) {
            onClose();
          }
        }}
      >
        {content}
      </div>
    );
  }

  return content;
}
