import { useState, useEffect, useRef, useLayoutEffect } from "react";
import {
  getToken,
  setToken,
  removeToken,
  setRefreshToken,
} from "../utils/storage";
import { apiClient } from "../config/api";

const BASE_URL = "http://localhost:8000";

interface Agent {
  id: string;
  context?: { name?: string };
}

interface Action {
  id: string;
  name?: string;
  context?: { label?: string };
}

interface Interaction {
  id?: string;
  data: {
    user_prompt: string;
    system_prompt: string;
    model: string;
    response: string;
    history?: any[];
  };
}

interface ParentInteraction {
  id: string;
  utterance?: string;
  metrics: any[];
}

export function DebugInteractions() {
  const [authToken, setAuthToken] = useState<string | null>(null);

  // Data state
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [parentInteractions, setParentInteractions] = useState<
    ParentInteraction[]
  >([]);
  const [modelAction, setModelAction] = useState<Action | null>(null);

  // Selection state
  const [selectedInteraction, setSelectedInteraction] =
    useState<Interaction | null>(null);
  const [selectedParentIndex, setSelectedParentIndex] = useState<number | null>(
    null,
  );
  const [selectedMetricIndex, setSelectedMetricIndex] = useState<number | null>(
    null,
  );

  // UI state
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");

  const userRef = useRef<HTMLTextAreaElement | null>(null);
  const systemRef = useRef<HTMLTextAreaElement | null>(null);
  const historyRef = useRef<HTMLTextAreaElement | null>(null);
  const [darkMode, setDarkMode] = useState(false);
  const [historyText, setHistoryText] = useState<string>("");

  const adjustHeight = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    // Store current scroll position to avoid jump
    // const currentScroll = window.scrollY;

    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;

    // Restore scroll if needed, but 'auto' can cause layout shift before we restore.
    // window.scrollTo({ top: currentScroll, behavior: 'auto' });
  };

  const preserveScroll = (fn: () => void) => {
    if (typeof window === "undefined") {
      fn();
      return;
    }
    const y = window.scrollY || window.pageYOffset || 0;
    fn();
    // restore after render
    setTimeout(() => window.scrollTo({ top: y, behavior: "auto" }), 0);
  };

  useEffect(() => {
    const storedToken = getToken();
    if (storedToken) {
      setAuthToken(storedToken);
      initializeDebugSession(storedToken);
    }
  }, []);

  const lastFocusedInteractionId = useRef<string | null>(null);

  // Effect for initialization when loading a NEW interaction
  useEffect(() => {
    // set history editable text
    setHistoryText(
      selectedInteraction && selectedInteraction.data.history
        ? JSON.stringify(selectedInteraction.data.history, null, 2)
        : "",
    );

    // Only focus if we switched to a DIFFERENT interaction
    if (selectedInteraction && selectedInteraction.id) {
      if (selectedInteraction.id !== lastFocusedInteractionId.current) {
        lastFocusedInteractionId.current = selectedInteraction.id;
        setTimeout(() => userRef.current?.focus?.(), 0);
      }
    } else {
      lastFocusedInteractionId.current = null;
    }
  }, [selectedInteraction?.id]); // Only runs when ID changes (or interaction becomes null)

  // Layout Effect for resizing textareas when content changes
  // We separate this so it runs synchronously after DOM update, preventing visual jump
  useLayoutEffect(() => {
    adjustHeight(userRef.current);
  }, [selectedInteraction?.data.user_prompt]);

  useLayoutEffect(() => {
    adjustHeight(systemRef.current);
  }, [selectedInteraction?.data.system_prompt]);

  useLayoutEffect(() => {
    adjustHeight(historyRef.current);
  }, [historyText, selectedInteraction?.data.history]); // Check history text or data

  const initializeDebugSession = async (authToken: string) => {
    setLoading(true);
    setStatusMessage("Initializing session...");
    try {
      // 1. Get Agents and find resolv_demo
      const agentsRes = await fetch(
        `${BASE_URL}/api/agents?page=1&per_page=10`,
        {
          headers: { Authorization: `Bearer ${authToken}` },
        },
      );
      const agentsData = await agentsRes.json();
      // Prefer 'resolv_demo', fallback to first available
      const targetAgent =
        agentsData.agents.find(
          (a: Agent) => a.context?.name === "resolv_demo",
        ) || agentsData.agents[0];

      if (!targetAgent) {
        throw new Error("No agents found");
      }

      setStatusMessage(
        `Selected Agent: ${targetAgent.context?.name || targetAgent.id}`,
      );

      // 2. Get Actions for the agent
      const actionsRes = await fetch(
        `${BASE_URL}/api/agents/${targetAgent.id}/actions?page=1&per_page=50&enabled_only=false`,
        {
          headers: { Authorization: `Bearer ${authToken}` },
        },
      );
      const actionsData = await actionsRes.json();
      const actions = actionsData.actions;

      // 3. Find specific actions
      const utilsAction = actions.find(
        (a: Action) => a.context?.label === "agent_utils",
      );
      const modelAction = actions.find(
        (a: Action) => a.context?.label === "openai_lm",
      );

      if (!utilsAction) throw new Error("Could not find 'agent_utils' action");
      if (!modelAction) throw new Error("Could not find 'openai_lm' action");

      setModelAction(modelAction);

      // 4. Load interactions from agent_utils
      setStatusMessage("Loading interactions...");
      const interactRes = await fetch(
        `${BASE_URL}/api/actions/${utilsAction.id}/interactions`,
        {
          headers: { Authorization: `Bearer ${authToken}` },
        },
      );
      const interactData = await interactRes.json();

      const parents = interactData.interactions
        .filter((i: any) => i.context?.log_level === "INTERACTION")
        .map((i: any) => {
          const metrics =
            i.context?.log_data?.interaction_data?.observability_metrics || [];
          const utterance = i.context?.log_data?.interaction_data?.utterance;
          return { id: i.id, utterance, metrics } as ParentInteraction;
        })
        .filter((p: ParentInteraction) => p.metrics && p.metrics.length > 0);

      setParentInteractions(parents);

      // Create normalized flattened interactions (only model_call metrics)
      const flattened = parents.flatMap((p) =>
        p.metrics
          .filter((m: any) =>
            (m.event_type || "")
              .toString()
              .toLowerCase()
              .includes("model_call"),
          )
          .map((m: any) => {
            const d = m.data || {};
            return {
              id: m.id,
              data: {
                user_prompt: d.user_prompt || d.prompt || "",
                system_prompt: d.system_prompt || "",
                model: d.model || "",
                history: d.history || [],
              },
            } as Interaction;
          }),
      );

      setInteractions(flattened);

      // Default to the first parent interaction and its first observability metric
      if (parents.length > 0) {
        const firstParentIdx = 0;
        setSelectedParentIndex(firstParentIdx);

        const firstMetrics = parents[firstParentIdx].metrics;
        const firstMetricIdx = firstMetrics.length > 0 ? 0 : null;
        setSelectedMetricIndex(firstMetricIdx);

        if (firstMetricIdx !== null) {
          const picked = firstMetrics[firstMetricIdx];
          const pd = picked.data || {};
          setSelectedInteraction({
            id: picked.id,
            data: {
              user_prompt: pd.user_prompt || pd.prompt || "",
              system_prompt: pd.system_prompt || "",
              response: pd.response || "",
              model: pd.model || "",
              history: pd.history || [],
            },
          });
        }
      }

      setStatusMessage("");
    } catch (err: any) {
      console.error(err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

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

      const response = await fetch(
        `${BASE_URL}/api/actions/${modelAction.id}/query`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${authToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        },
      );

      const data = await response.json();
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

  const truncate = (str: string, length = 100) => {
    if (!str) return "";
    return str.length > length ? str.substring(0, length) + "..." : str;
  };

  return (
    <div
      className={`min-h-screen p-6 ${darkMode ? "bg-gray-900 text-gray-100" : "bg-gray-50 text-gray-900"}`}
    >
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-6">
          <div className="flex items-center space-x-4">
            {/* <h1
              className={`text-3xl font-bold ${darkMode ? "text-gray-100" : "text-gray-900"}`}
            ></h1> */}

            <div
              className={`${darkMode ? "bg-gray-800" : "bg-white"} rounded shadow p-2 flex items-center space-x-2`}
            >
              {loading ? (
                <div className="text-gray-500 px-3">
                  Loading interactions...
                </div>
              ) : parentInteractions.length > 0 ? (
                <>
                  <select
                    className="w-96 p-2 border border-gray-300 rounded text-sm"
                    value={selectedParentIndex ?? ""}
                    onChange={(e) => {
                      const idx = parseInt(e.target.value);
                      preserveScroll(() => {
                        setSelectedParentIndex(idx);
                        const metrics = parentInteractions[idx].metrics;
                        // select the first metric (observability metric) to mirror test_interaction.py behavior
                        const metricIdx = metrics.length > 0 ? 0 : null;
                        setSelectedMetricIndex(metricIdx);
                        if (metricIdx !== null) {
                          const picked = metrics[metricIdx];
                          const pd = picked.data || {};
                          setSelectedInteraction({
                            id: picked.id,
                            data: {
                              user_prompt: pd.user_prompt || pd.prompt || "",
                              system_prompt: pd.system_prompt || "",
                              response: pd.response || "",
                              model: pd.model || "",
                              history: pd.history || [],
                            },
                          });
                        }
                      });
                    }}
                  >
                    {parentInteractions.map((p, index) => (
                      <option key={p.id || index} value={index}>
                        [{index}]{" "}
                        {truncate(p.utterance || "(no utterance)", 120)}
                      </option>
                    ))}
                  </select>

                  {/* LLM call selector for chosen interaction */}
                  <select
                    className="w-80 p-2 border border-gray-300 rounded text-sm"
                    value={selectedMetricIndex ?? ""}
                    onChange={(e) => {
                      const mIdx = parseInt(e.target.value);
                      preserveScroll(() => {
                        setSelectedMetricIndex(mIdx);
                        if (selectedParentIndex != null) {
                          const metric =
                            parentInteractions[selectedParentIndex].metrics[
                              mIdx
                            ];
                          const md = metric.data || {};
                          setSelectedInteraction({
                            id: metric.id,
                            data: {
                              user_prompt: md.user_prompt || md.prompt || "",
                              system_prompt: md.system_prompt || "",
                              response: md.response || "",
                              model: md.model || "",
                              history: md.history || [],
                            },
                          });
                        }
                      });
                    }}
                  >
                    {selectedParentIndex != null &&
                      parentInteractions[selectedParentIndex].metrics.map(
                        (m: any, mi: number) => (
                          <option key={mi} value={mi}>
                            [{mi}]{" "}
                            {truncate(
                              JSON.stringify(m.data?.response || m.data || m),
                              80,
                            )}
                          </option>
                        ),
                      )}
                  </select>
                </>
              ) : (
                <div className="text-gray-500 px-3">No interactions found</div>
              )}
            </div>
          </div>

          <div className="flex items-center space-x-2">
            {statusMessage && (
              <span className="text-gray-500 text-sm">{statusMessage}</span>
            )}

            <button
              type="button"
              onClick={() => initializeDebugSession(getToken() || "")}
              className={`px-3 py-1 rounded text-sm ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"}`}
            >
              Refresh
            </button>

            <button
              type="button"
              onClick={async () => {
                // prompt for credentials to auto-login
                // const serverUrl = window.prompt('Server URL (leave blank to keep current):');
                // const email = window.prompt('Email (admin@jvagent.example):', 'admin@jvagent.example');
                // const password = window.prompt('Password:');
                const email = "admin@jvagent.example";
                const password = "your-admin-password-here";
                const serverUrl = "http://localhost:8000";
                if (!email || !password) return;
                try {
                  const creds: any = { email, password };
                  if (serverUrl) creds.serverUrl = serverUrl;
                  const resp = await apiClient.login(creds as any);
                  if (resp && resp.access_token) {
                    // persist tokens to storage and update local auth state
                    setToken(resp.access_token);
                    setAuthToken(resp.access_token);
                    if ((resp as any).refresh_token)
                      setRefreshToken((resp as any).refresh_token);
                    initializeDebugSession(resp.access_token);
                  }
                } catch (err: any) {
                  console.error("Re-auth failed", err);
                  alert("Re-auth failed: " + (err?.message || err));
                }
              }}
              className={`px-3 py-1 rounded text-sm ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"}`}
            >
              Re-auth
            </button>

            <button
              type="button"
              onClick={() => setDarkMode(!darkMode)}
              className={`px-3 py-1 rounded text-sm ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"}`}
            >
              {darkMode ? "Light" : "Dark"}
            </button>
            <div className="border-l border-gray-300 h-6 mx-2" />
            <button
              type="button"
              onClick={() => {
                if (!selectedInteraction) return;
                const dataToExport = {
                  interaction: selectedInteraction,
                  testResult: testResult,
                };
                const blob = new Blob([JSON.stringify(dataToExport, null, 2)], {
                  type: "application/json",
                });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `interaction_${selectedInteraction.id || "export"}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
              }}
              disabled={!selectedInteraction}
              className={`px-3 py-1 rounded text-sm disabled:opacity-50 ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"}`}
            >
              Export
            </button>
            <label
              className={`px-3 py-1 rounded text-sm cursor-pointer ${darkMode ? "bg-gray-700 hover:bg-gray-600" : "bg-gray-100 hover:bg-gray-200"}`}
            >
              Import
              <input
                type="file"
                accept=".json"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  const reader = new FileReader();
                  reader.onload = (event) => {
                    try {
                      const content = event.target?.result as string;
                      const parsed = JSON.parse(content);

                      // Handle both new format (with testResult) and potential legacy format (just interaction)
                      const interactionData = parsed.interaction || parsed;
                      const testResultData = parsed.testResult || null;

                      if (interactionData && interactionData.data) {
                        preserveScroll(() => {
                          // Detach from parent list
                          setSelectedParentIndex(null);
                          setSelectedMetricIndex(null);

                          setSelectedInteraction(interactionData);
                          setTestResult(testResultData);
                          setStatusMessage("Imported interaction successfully");
                        });
                      } else {
                        setError("Invalid import file format");
                      }
                    } catch (err) {
                      console.error("Import failed", err);
                      setError("Failed to parse import file");
                    }
                    // Reset input so same file can be selected again if needed
                    e.target.value = "";
                  };
                  reader.readAsText(file);
                }}
              />
            </label>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4">
            {error}
          </div>
        )}

        {/* Selector moved to header */}

        {/* Details and Test Area */}
        {selectedInteraction ? (
          <div
            className={`${darkMode ? "bg-gray-800" : "bg-white"} rounded-lg shadow p-6`}
          >
            <div className="space-y-6">
              <div
                className={`p-4 rounded text-sm bg-red-50 border border-gray-200`}
              >
                {selectedInteraction.data.response && (
                  <div>
                    <div className="text-gray-700 whitespace-pre-wrap font-mono">
                      {selectedInteraction.data.response}
                    </div>
                  </div>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
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
                  rows={6}
                  style={{ overflow: "hidden" }}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
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
                  rows={8}
                  style={{ overflow: "hidden" }}
                />
              </div>
              {/* History (editable) */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  History (JSON)
                </label>
                <textarea
                  ref={historyRef}
                  value={historyText}
                  onChange={(e) => {
                    setHistoryText(e.target.value);
                  }}
                  onBlur={() => {
                    try {
                      const parsed = historyText ? JSON.parse(historyText) : [];
                      setSelectedInteraction((si) =>
                        si
                          ? { ...si, data: { ...si.data, history: parsed } }
                          : si,
                      );
                    } catch (err) {
                      // Keep user text; do not overwrite
                      console.error("Invalid JSON for history:", err);
                    }
                  }}
                  className={`w-full p-3 border rounded text-sm font-mono ${darkMode ? "bg-gray-900 border-gray-700 text-gray-100" : "bg-gray-100 border-gray-200 text-gray-900"}`}
                  rows={6}
                  style={{ overflow: "hidden" }}
                />
              </div>
              <div className="space-y-6">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
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
                    className="w-full p-2 border border-gray-300 rounded text-sm font-mono"
                  />
                </div>

                {/* <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Active Action
                  </label>
                  <div className="p-2 bg-gray-100 rounded text-gray-700 text-sm">
                    {modelAction?.name || "No Model Action Selected"}
                  </div>
                </div> */}
              </div>

              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-semibold text-gray-900">
                  Details & Test
                </h2>
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testing || !modelAction}
                  className={`px-6 py-2 rounded font-medium disabled:opacity-50 ${darkMode ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-blue-600 text-white hover:bg-blue-700"}`}
                >
                  {testing ? "Testing..." : "Run Test"}
                </button>
              </div>

              {testResult ? (
                <div className="mt-6 border-t pt-6">
                  <h3 className="text-lg font-medium text-gray-900 mb-4">
                    Test Result
                  </h3>
                  <div
                    className={`p-4 rounded text-sm ${
                      testResult.success
                        ? "bg-green-50 border border-green-200"
                        : "bg-red-50 border border-red-200"
                    }`}
                  >
                    {testResult.success ? (
                      <div>
                        <div className="text-green-700 whitespace-pre-wrap font-mono">
                          {testResult.response}
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div className="font-medium text-red-800 mb-2">
                          Error
                        </div>
                        <div className="text-red-700">{testResult.error}</div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="mt-6 border-t pt-6 invisible">
                  <h3 className="text-lg font-medium text-gray-900 mb-4">
                    Test Result
                  </h3>
                  <div className="p-4 rounded h-24 border border-transparent" />
                </div>
              )}

              <div className="mt-6 border-t pt-6">
                <h3 className="text-lg font-medium text-gray-900 mb-4">
                  Response
                </h3>
                <div
                  className={`p-4 rounded text-sm bg-red-50 border border-gray-200`}
                >
                  {selectedInteraction.data.response && (
                    <div>
                      <div className="text-gray-700 whitespace-pre-wrap font-mono">
                        {selectedInteraction.data.response}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : (
          !loading && (
            <div
              className={`${darkMode ? "bg-gray-800 text-gray-200" : "bg-white text-gray-500"} rounded-lg shadow p-12 text-center`}
            >
              No interaction selected or available.
            </div>
          )
        )}
      </div>
    </div>
  );
}
