import {
  useState,
  useEffect,
  useRef,
  useCallback,
  startTransition,
  useMemo,
} from "react";
import { useParams, useNavigate, Navigate } from "react-router-dom";
import { PanelLeft, PanelRight, Sun, Moon, Clipboard } from "lucide-react";
import { useAgents } from "../hooks/useAgents";
import {
  useStreaming,
  type SendMessageOptions,
  ATTACHMENT_ONLY_USER_PROMPT,
} from "../hooks/useStreaming";
import { useConversations } from "../hooks/useConversations";
import { Thread } from "./Thread";
import { ConversationList } from "./ConversationList";
import { DebugInteractions } from "./DebugInteractions";
import { PageIndexDocumentsModal } from "./PageIndexDocumentsModal";
import { ActionsModal } from "./ActionsModal";
import { MemoryViewer } from "./MemoryViewer";
import {
  getMessages,
  deleteMessages,
  getConversations,
  getEffectiveUserId,
  getToken,
} from "../utils/storage";
import { apiClient } from "../config/api";
import type { Conversation } from "../types/conversation";
import { useOpenAppGraph } from "../context/AppGraphContext";
import { useTheme } from "../context/ThemeContext";
import { ComposerToolsMenu } from "./ComposerToolsMenu";
import { AgentSwitcher } from "./AgentSwitcher";

export function ChatInterface() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const openAppGraph = useOpenAppGraph();
  const { theme, toggleTheme } = useTheme();
  const { agents, loading: agentsLoading } = useAgents();
  const agent = agents.find((a) => a.id === agentId);
  const [sessionId, setSessionId] = useState<string | undefined>();

  const { conversations, add, update, remove, refresh } =
    useConversations(agentId);
  const {
    messages,
    sendMessage,
    stopStreaming,
    clearMessages,
    loadMessages,
    isStreaming,
    error,
    sessionId: streamSessionId,
    editAndResend,
    selectBranchVersion,
    branchSnapshots,
    branchVersionIndex,
  } = useStreaming(agentId || "", sessionId);

  const viewingOldBranch = useMemo(() => {
    for (const rootId of Object.keys(branchSnapshots)) {
      const snaps = branchSnapshots[rootId];
      if (!snaps || snaps.length < 2) continue;
      const idx = branchVersionIndex[rootId] ?? snaps.length - 1;
      if (idx < snaps.length - 1) return true;
    }
    return false;
  }, [branchSnapshots, branchVersionIndex]);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);
  const [isMdUp, setIsMdUp] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth >= 768 : true,
  );
  const [isDebugModalOpen, setIsDebugModalOpen] = useState(false);
  const [isPageIndexModalOpen, setIsPageIndexModalOpen] = useState(false);
  const [isActionsModalOpen, setIsActionsModalOpen] = useState(false);
  const [isMemoryModalOpen, setIsMemoryModalOpen] = useState(false);
  const [hasPageIndexAction, setHasPageIndexAction] = useState(false);

  const handleMobileMenuClose = useCallback(() => {
    setIsMobileMenuOpen(false);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const sync = () => {
      const up = mq.matches;
      setIsMdUp(up);
      if (up) setDesktopSidebarOpen(true);
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const handleNavRailToggle = useCallback(() => {
    if (isMdUp) {
      setDesktopSidebarOpen((v) => !v);
    } else {
      setIsMobileMenuOpen(true);
    }
  }, [isMdUp]);

  const handleCopyToken = useCallback(() => {
    const token = getToken();
    if (token) void navigator.clipboard.writeText(token);
  }, []);

  const handleToggleDebugModal = useCallback(() => {
    setIsDebugModalOpen((prev) => !prev);
  }, []);

  const handleCloseDebugModal = useCallback(() => {
    setIsDebugModalOpen(false);
  }, []);

  const handleTogglePageIndexModal = useCallback(() => {
    setIsPageIndexModalOpen((prev) => !prev);
  }, []);

  const handleClosePageIndexModal = useCallback(() => {
    setIsPageIndexModalOpen(false);
  }, []);

  const handleToggleActionsModal = useCallback(() => {
    setIsActionsModalOpen((prev) => !prev);
  }, []);

  const handleCloseActionsModal = useCallback(() => {
    setIsActionsModalOpen(false);
  }, []);

  const handleToggleMemoryModal = useCallback(() => {
    setIsMemoryModalOpen((prev) => !prev);
  }, []);

  const handleCloseMemoryModal = useCallback(() => {
    setIsMemoryModalOpen(false);
  }, []);

  useEffect(() => {
    if (!agentId) {
      setHasPageIndexAction(false);
      return;
    }
    apiClient
      .getActions(agentId)
      .then((data) => {
        const actions = data.actions || data || [];
        const has = actions.some(
          (a: any) =>
            a.label === "pageindex_retrieval_interact_action" ||
            a.context?.label === "pageindex_retrieval_interact_action" ||
            (a.entity && String(a.entity).includes("PageIndexRetrievalInteractAction")) ||
            (a.archetype && String(a.archetype).includes("PageIndexRetrievalInteractAction")) ||
            (a.action === "jvagent/pageindex_retrieval_interact_action")
        );
        const hasFallback = !has && actions.some(
          (a: any) =>
            a.label === "pageindex_action" ||
            a.context?.label === "pageindex_action" ||
            (a.entity && String(a.entity).includes("PageIndexAction")) ||
            (a.archetype && String(a.archetype).includes("PageIndexAction")) ||
            (a.action === "jvagent/pageindex_action")
        );
        const finalHas = has || hasFallback;
        setHasPageIndexAction(finalHas);
      })
      .catch(() => setHasPageIndexAction(false));
  }, [agentId]);

  useEffect(() => {
    refresh();
  }, [agentId, refresh]);

  useEffect(() => {
    const handleFocus = () => {
      refresh();
    };
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [refresh]);

  useEffect(() => {
    if (!agentId) {
      navigate("/chat");
      return;
    }
    if (agents.length > 0 && !agent) {
      navigate("/chat");
    }
  }, [agentId, agents, agent, navigate]);

  useEffect(() => {
    setSessionId(undefined);
  }, [agentId]);

  const prevStreamSessionIdRef = useRef<string | undefined>(streamSessionId);
  // Tracks whether the pending setSessionId call was triggered by the SSE stream
  // (server assigned a session id) vs. by the user selecting/navigating to a session.
  // Only stream-triggered bindings should skip the clear+load sequence.
  const sessionSetByStreamRef = useRef(false);
  useEffect(() => {
    if (
      streamSessionId &&
      streamSessionId !== prevStreamSessionIdRef.current &&
      streamSessionId !== sessionId
    ) {
      prevStreamSessionIdRef.current = streamSessionId;
      sessionSetByStreamRef.current = true;
      setSessionId(streamSessionId);
    } else if (streamSessionId) {
      prevStreamSessionIdRef.current = streamSessionId;
    }
  }, [streamSessionId, sessionId]);

  const prevSessionIdRef = useRef<string | undefined>(sessionId);

  useEffect(() => {
    if (sessionId !== prevSessionIdRef.current) {
      const newSessionId = sessionId;
      const oldSessionId = prevSessionIdRef.current;
      console.log(
        `Switching conversation: ${oldSessionId || "none"} -> ${newSessionId || "none"}`,
      );
      prevSessionIdRef.current = sessionId;

      // Server assigned first session id while messages are already live — avoid clear + reload flicker.
      // This only applies when the stream itself triggered the session id change; if the user
      // selected or navigated to an existing session we must still load from localStorage.
      const sessionSetByStream = sessionSetByStreamRef.current;
      sessionSetByStreamRef.current = false;
      const isInitialSessionBinding =
        oldSessionId === undefined && newSessionId !== undefined && sessionSetByStream;

      if (newSessionId) {
        if (isInitialSessionBinding) {
          return;
        }
        clearMessages();
        const timer = setTimeout(() => {
          if (prevSessionIdRef.current === newSessionId) {
            const savedMessages = getMessages(newSessionId);
            console.log(
              `Loading ${savedMessages.length} messages for session ${newSessionId}`,
            );
            if (savedMessages && savedMessages.length > 0) {
              if (prevSessionIdRef.current === newSessionId) {
                loadMessages(savedMessages);
              } else {
                console.warn(
                  `Session changed during load delay - skipping load for ${newSessionId}`,
                );
              }
            } else {
              console.log(
                `No saved messages found for session ${newSessionId}`,
              );
            }
          } else {
            console.warn(
              `Session changed during load delay - skipping load for ${newSessionId}`,
            );
          }
        }, 50);
        return () => clearTimeout(timer);
      } else {
        console.log("Starting new conversation - clearing messages");
        clearMessages();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handleSendMessage = async (
    content: string,
    options?: SendMessageOptions,
  ) => {
    if (!agent) return;
    const userId = getEffectiveUserId();
    if (!userId) {
      console.error(
        "Cannot send message: no user_id available. User should be logged in.",
      );
      return;
    }
    const lastPreview =
      content.trim() ||
      (options?.files?.length ? ATTACHMENT_ONLY_USER_PROMPT : content);
    const receivedSessionId = await sendMessage(content, options);
    if (receivedSessionId) {
      const allConversations = getConversations(userId);
      const existingConv = allConversations.find(
        (c) => c.session_id === receivedSessionId && c.agent_id === agent.id,
      );
      const sessionIdChanged = receivedSessionId !== sessionId;
      if (sessionIdChanged) {
        setSessionId(receivedSessionId);
      }
      if (!existingConv) {
        const newConv: Conversation = {
          session_id: receivedSessionId,
          agent_id: agent.id,
          agent_name: agent.alias || agent.name || "Agent",
          created_at: new Date().toISOString(),
          last_message: lastPreview,
          last_message_at: new Date().toISOString(),
        };
        startTransition(() => {
          add(newConv);
          console.log(
            `Created new conversation: ${receivedSessionId} for agent ${agent.id}`,
          );
        });
      } else {
        if (existingConv.last_message !== lastPreview) {
          startTransition(() => {
            update(receivedSessionId, {
              last_message: lastPreview,
              last_message_at: new Date().toISOString(),
            });
          });
        }
      }
    } else if (sessionId) {
      const allConversations = getConversations(userId);
      const currentConv = allConversations.find(
        (c) => c.session_id === sessionId && c.agent_id === agent.id,
      );
      if (currentConv && currentConv.last_message !== lastPreview) {
        startTransition(() => {
          update(sessionId, {
            last_message: lastPreview,
            last_message_at: new Date().toISOString(),
          });
        });
      }
    }
  };

  const handleNewConversation = useCallback(() => {
    if (!agent) return;
    console.log("Starting new conversation");
    setSessionId(undefined);
    refresh();
  }, [agent, refresh]);

  const handleSelectConversation = useCallback(
    (selectedSessionId: string) => {
      if (selectedSessionId === sessionId) return;
      console.log(
        `Switching conversation from ${sessionId || "none"} to ${selectedSessionId}`,
      );
      setSessionId(selectedSessionId);
    },
    [sessionId],
  );

  const handleDeleteConversation = async (sessionIdToDelete: string) => {
    if (!agent) return;
    const userId = getEffectiveUserId();
    if (!userId) {
      console.error("Cannot delete conversation: user_id not found");
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
      return;
    }
    try {
      await apiClient.deleteConversation(agent.id, userId, sessionIdToDelete);
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
    } catch (error: any) {
      console.error("Failed to delete conversation on server:", error);
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
    }
  };

  if (!agent) {
    if (agentsLoading) {
      return (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-zinc-400 mx-auto"></div>
            <p className="mt-4 text-zinc-500 dark:text-zinc-400">Loading agent...</p>
          </div>
        </div>
      );
    }
    // getAgents finished without this agent: either session was cleared (401) or unknown id
    if (!getToken()) {
      return <Navigate to="/login" replace />;
    }
    return <Navigate to="/chat" replace />;
  }

  const composerTools = (
    <ComposerToolsMenu
      disabled={isStreaming || viewingOldBranch}
      hasDocuments={hasPageIndexAction}
      onDocuments={handleTogglePageIndexModal}
      onInteractionDebug={handleToggleDebugModal}
      onActionConfig={handleToggleActionsModal}
      onLongMemory={handleToggleMemoryModal}
      onAppGraph={openAppGraph}
    />
  );

  const storedToken = getToken();

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <ConversationList
          conversations={conversations}
          currentSessionId={sessionId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onDeleteConversation={handleDeleteConversation}
          isMobileMenuOpen={isMobileMenuOpen}
          onMobileMenuClose={handleMobileMenuClose}
          desktopSidebarOpen={desktopSidebarOpen}
        />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="relative z-20 shrink-0 overflow-visible border-b border-zinc-200 bg-white dark:border-white/10 dark:bg-zinc-900">
            <div className="flex h-[4.75rem] w-full shrink-0 items-center justify-between gap-2 overflow-visible px-4 sm:gap-3 sm:px-5">
              <div className="flex min-w-0 flex-1 items-center gap-2 overflow-visible">
                <button
                  type="button"
                  onClick={handleNavRailToggle}
                  className="inline-flex size-10 shrink-0 touch-manipulation items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                  aria-label={
                    isMdUp
                      ? desktopSidebarOpen
                        ? "Collapse sidebar"
                        : "Expand sidebar"
                      : "Open sidebar"
                  }
                >
                  {isMdUp ? (
                    desktopSidebarOpen ? (
                      <PanelLeft className="h-5 w-5" aria-hidden strokeWidth={2} />
                    ) : (
                      <PanelRight className="h-5 w-5" aria-hidden strokeWidth={2} />
                    )
                  ) : (
                    <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                    </svg>
                  )}
                </button>
                <div className="flex w-[17rem] shrink-0 flex-col justify-center sm:w-[17.5rem]">
                  <div className="my-2.5 min-w-0 w-full">
                    <AgentSwitcher variant="full" />
                  </div>
                </div>
                <div className="ml-1 flex min-w-[5.75rem] shrink-0 flex-col items-start justify-center gap-0.5 border-l border-zinc-200 pl-2 dark:border-white/10 sm:ml-2 sm:min-w-[6rem] sm:pl-3">
                  <button
                    type="button"
                    disabled={!sessionId}
                    onClick={() => {
                      if (!sessionId) return;
                      void navigator.clipboard.writeText(sessionId);
                    }}
                    aria-label="Copy session ID"
                    title={sessionId ? "Copy session ID" : "No active session"}
                    className="flex w-full items-center gap-1.5 text-left text-xs text-zinc-500 transition-colors hover:text-zinc-700 disabled:pointer-events-none disabled:opacity-35 dark:text-zinc-400 dark:hover:text-zinc-200"
                  >
                    <Clipboard className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden strokeWidth={1.75} />
                    <span>session ID</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleCopyToken()}
                    disabled={!storedToken}
                    aria-label="Copy token"
                    title={storedToken ? "Copy access token" : "No token stored"}
                    className="flex w-full items-center gap-1.5 text-left text-xs text-zinc-500 transition-colors hover:text-zinc-700 disabled:pointer-events-none disabled:opacity-35 dark:text-zinc-400 dark:hover:text-zinc-200"
                  >
                    <Clipboard className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden strokeWidth={1.75} />
                    <span>token</span>
                  </button>
                </div>
              </div>
              <button
                type="button"
                onClick={toggleTheme}
                className="inline-flex size-10 shrink-0 touch-manipulation items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              >
                {theme === "dark" ? (
                  <Sun className="size-5" strokeWidth={1.75} aria-hidden />
                ) : (
                  <Moon className="size-5" strokeWidth={1.75} aria-hidden />
                )}
              </button>
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
            <Thread
              messages={messages}
              isStreaming={isStreaming}
              showThinking={
                isStreaming &&
                !messages.some(
                  (m) =>
                    m.role === "assistant" &&
                    m.category !== "thought" &&
                    m.streaming,
                )
              }
              onEditMessage={(id, text) => editAndResend(id, text)}
              branchSnapshots={branchSnapshots}
              branchVersionIndex={branchVersionIndex}
              onBranchVersionChange={selectBranchVersion}
              onSend={handleSendMessage}
              onStop={stopStreaming}
              composerDisabled={isStreaming || viewingOldBranch}
              composerMenu={composerTools}
              placeholder={`Message ${agent.alias || agent.name || "Agent"}...`}
              welcomeAgentName={agent.alias || agent.name || "Agent"}
              welcomeAgentAvatar={agent.avatar_url}
              welcomeDescription={agent.description}
            />
          </div>

          {error && (
            <div className="flex-shrink-0 px-4 py-2.5 bg-red-50 dark:bg-red-900/20 border-t border-red-200 dark:border-red-800">
              <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
            </div>
          )}
        </div>

        {isDebugModalOpen && (
          <DebugInteractions
            onClose={handleCloseDebugModal}
            isEmbedded={true}
          />
        )}

        {isPageIndexModalOpen && agentId && (
          <PageIndexDocumentsModal
            agentId={agentId}
            onClose={handleClosePageIndexModal}
            isEmbedded={true}
          />
        )}

        {isActionsModalOpen && agentId && (
          <ActionsModal
            agentId={agentId}
            onClose={handleCloseActionsModal}
            isEmbedded={true}
          />
        )}

        {isMemoryModalOpen && agentId && (
          <MemoryViewer
            agentId={agentId}
            onClose={handleCloseMemoryModal}
          />
        )}
      </div>
    </div>
  );
}
