import {
  useState,
  useEffect,
  useRef,
  useLayoutEffect,
  useCallback,
} from "react";
import { apiClient } from "../config/api";

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
  const [selectedInteraction, setSelectedInteraction] = useState<any>(null);
  const [selectedParentIndex, setSelectedParentIndex] = useState<number | null>(
    null,
  );
  const [selectedMetricIndex, setSelectedMetricIndex] = useState<number | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [darkMode, setDarkMode] = useState(false);
  const [historyText, setHistoryText] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  const userRef = useRef<HTMLTextAreaElement>(null);
  const systemRef = useRef<HTMLTextAreaElement>(null);
  const historyRef = useRef<HTMLTextAreaElement>(null);
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

  const initializeDebugSession = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const agentsData = await apiClient.getAgents();
      if (!agentsData || !agentsData.agents) throw new Error("No agents found");

      const targetAgent =
        agentsData.agents.find((a: any) => a.context?.name === "resolv_demo") ||
        agentsData.agents[0];

      if (!targetAgent) throw new Error("No agents found");

      const actionsData = await apiClient.getActions(targetAgent.id);
      const actions = actionsData.actions;

      const utilsAction = actions.find(
        (a: any) => a.context?.label === "agent_utils",
      );
      const modelActionItem = actions.find(
        (a: any) => a.context?.label === "openai_lm",
      );

      if (!utilsAction) throw new Error("Could not find 'agent_utils' action");
      if (!modelActionItem)
        throw new Error("Could not find 'openai_lm' action");

      setModelAction(modelActionItem);

      const interactData = await apiClient.getInteractions(utilsAction.id);

      const parents = interactData.interactions
        .filter((i: any) => i.context?.log_level === "INTERACTION")
        .map((i: any) => {
          const metrics =
            i.context?.log_data?.interaction_data?.observability_metrics || [];
          const utterance = i.context?.log_data?.interaction_data?.utterance;
          const conversationHistory =
            i.context?.log_data?.interaction_data?.conversation_history || [];
          return { id: i.id, utterance, metrics, conversationHistory };
        })
        .filter((p: any) => p.metrics && p.metrics.length > 0);

      setParentInteractions(parents);

      if (parents.length > 0) {
        // Automatically select the latest interaction (first item as API returns newest first)
        const latestParentIdx = 0;
        const latestMetricIdx = parents[latestParentIdx].metrics.length - 1;
        selectInteraction(latestParentIdx, latestMetricIdx, parents);
      }
    } catch (err: any) {
      console.error(err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [selectInteraction]);

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
  }, [selectedInteraction?.data.user_prompt]);

  useLayoutEffect(() => {
    adjustHeight(systemRef.current);
  }, [selectedInteraction?.data.system_prompt]);

  useLayoutEffect(() => {
    if (showHistory) {
      adjustHeight(historyRef.current);
    }
  }, [historyText, showHistory]);

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

  const handleExport = () => {
    if (!selectedInteraction) return;

    const dataToExport = {
      interaction: selectedInteraction,
      testResult: testResult,
      metadata: {
        exportedAt: new Date().toISOString(),
        parentIndex: selectedParentIndex,
        metricIndex: selectedMetricIndex,
      },
    };

    const blob = new Blob([JSON.stringify(dataToExport, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `interaction_${selectedInteraction.id || new Date().getTime()}.json`;
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

  const content = (
    <div
      className={`${isEmbedded ? "bg-white rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col" : `min-h-screen p-6 ${darkMode ? "bg-gray-900 text-gray-100" : "bg-gray-50 text-gray-900"}`}`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      <div
        ref={scrollContainerRef}
        className={`${isEmbedded ? "flex-1 overflow-y-auto p-6" : "max-w-full mx-auto"}`}
      >
        {/* Header */}
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold">Debug Interactions</h1>

          <div className="flex items-center space-x-2">
            {!isEmbedded && (
              <button
                onClick={() => setDarkMode(!darkMode)}
                className={`px-3 py-1.5 rounded text-sm ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${darkMode ? "border-gray-600" : "border-gray-300"}`}
              >
                {darkMode ? "☀️ Light" : "🌙 Dark"}
              </button>
            )}

            <button
              onClick={initializeDebugSession}
              disabled={loading}
              className={`px-3 py-1.5 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${darkMode ? "border-gray-600" : "border-gray-300"}`}
              title="Refresh Interactions"
            >
              🔄 Refresh
            </button>

            <button
              onClick={handleExport}
              disabled={!selectedInteraction}
              className={`px-3 py-1.5 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${darkMode ? "border-gray-600" : "border-gray-300"}`}
            >
              📤 Export
            </button>

            <label
              className={`px-3 py-1.5 rounded text-sm cursor-pointer ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-white hover:bg-gray-100"} border ${darkMode ? "border-gray-600" : "border-gray-300"}`}
            >
              📥 Import
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
                className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
                title="Close"
              >
                <svg
                  className="w-6 h-6"
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
        </div>

        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4 flex justify-between items-center">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="text-red-700 hover:text-red-900 font-bold"
            >
              ×
            </button>
          </div>
        )}

        {/* Interaction Selector */}
        {!loading && parentInteractions.length > 0 && (
          <div
            className={`${darkMode ? "bg-gray-800" : "bg-white"} rounded-lg shadow p-4 mb-6 border border-gray-100`}
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-2">
                  Interaction
                </label>
                <select
                  className={`w-full p-2 border rounded text-sm ${darkMode ? "bg-gray-700 border-gray-600 text-gray-100" : "bg-white border-gray-300"}`}
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
                <label className="block text-sm font-medium mb-2">
                  Response
                </label>
                <select
                  className={`w-full p-2 border rounded text-sm ${darkMode ? "bg-gray-700 border-gray-600 text-gray-100" : "bg-white border-gray-300"}`}
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
                            m.data?.response || m.data?.model || "Interaction",
                            100,
                          )}
                        </option>
                      ),
                    )}
                </select>
              </div>
            </div>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div
            className={`${darkMode ? "bg-gray-800" : "bg-white"} rounded-lg shadow p-12 text-center`}
          >
            <div className="text-lg">Loading interactions...</div>
          </div>
        )}

        {/* Main Content */}
        {!loading && selectedInteraction && (
          <div
            className={`${darkMode ? "bg-gray-800" : "bg-white"} rounded-lg shadow p-6 border border-gray-100`}
          >
            <div className="space-y-6">
              {/* Original Response */}
              {selectedInteraction.data.response && (
                <div>
                  <label className="block text-sm font-medium mb-2">
                    Original Response
                  </label>
                  <div
                    className={`p-4 rounded text-sm border ${darkMode ? "bg-gray-900 border-gray-700" : "bg-red-50 border-red-200"}`}
                  >
                    <pre className="whitespace-pre-wrap font-mono text-xs">
                      {selectedInteraction.data.response}
                    </pre>
                  </div>
                </div>
              )}
              {/* User Prompt */}
              <div>
                <label className="block text-sm font-medium mb-2">
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
                  className={`w-full p-3 border rounded text-sm font-mono ${darkMode ? "bg-blue-900 border-blue-700 text-gray-100" : "bg-blue-50 border-blue-200 text-gray-900"}`}
                  style={{ overflow: "hidden" }}
                />
              </div>
              {/* System Prompt */}
              <div>
                <label className="block text-sm font-medium mb-2">
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
                  className={`w-full p-3 border rounded text-sm font-mono ${darkMode ? "bg-yellow-900 border-yellow-700 text-gray-100" : "bg-yellow-50 border-yellow-200 text-gray-900"}`}
                  style={{ overflow: "hidden" }}
                />
              </div>
              {/* History - Only show if exists */}
              {showHistory && (
                <div>
                  <label className="block text-sm font-medium mb-2">
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
                    className={`w-full p-3 border rounded text-sm font-mono ${darkMode ? "bg-gray-900 border-gray-700 text-gray-100" : "bg-gray-100 border-gray-200 text-gray-900"}`}
                    style={{ overflow: "hidden" }}
                  />
                </div>
              )}
              {/* Model */}
              <div>
                <label className="block text-sm font-medium mb-2">Model</label>
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
                  className={`w-full p-2 border rounded text-sm font-mono ${darkMode ? "bg-gray-700 border-gray-600 text-gray-100" : "bg-white border-gray-300"}`}
                />
              </div>
              {/* Test Button */}
              <div className="flex justify-end">
                <button
                  onClick={handleTest}
                  disabled={testing || !modelAction}
                  className="px-6 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
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
                    className={`p-4 rounded text-sm border ${testResult.success ? (darkMode ? "bg-green-900 border-green-700" : "bg-green-50 border-green-200") : darkMode ? "bg-red-900 border-red-700" : "bg-red-50 border-red-200"}`}
                  >
                    {testResult.success ? (
                      <pre
                        className={`whitespace-pre-wrap font-mono text-xs ${darkMode ? "text-green-100" : "text-green-700"}`}
                      >
                        {testResult.response}
                      </pre>
                    ) : (
                      <div
                        className={darkMode ? "text-red-100" : "text-red-700"}
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
                    className={`p-4 rounded text-sm border ${darkMode ? "bg-gray-900 border-gray-700" : "bg-red-50 border-red-200"}`}
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

        {/* Empty State */}
        {!loading &&
          !selectedInteraction &&
          parentInteractions.length === 0 && (
            <div
              className={`${darkMode ? "bg-gray-800 text-gray-400" : "bg-white text-gray-500"} rounded-lg shadow p-12 text-center`}
            >
              No interactions available. Please check your connection and try
              refreshing the page.
            </div>
          )}
      </div>
    </div>
  );

  if (isEmbedded) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black bg-opacity-50"
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
