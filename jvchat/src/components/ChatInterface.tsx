import {
  useState,
  useEffect,
  useRef,
  useCallback,
  startTransition,
} from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAgents } from "../hooks/useAgents";
import { useStreaming } from "../hooks/useStreaming";
import { useConversations } from "../hooks/useConversations";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { WelcomeScreen } from "./WelcomeScreen";
import { ConversationList } from "./ConversationList";
import { DebugInteractions } from "./DebugInteractions";
import { PageIndexDocumentsModal } from "./PageIndexDocumentsModal";
import {
  getMessages,
  deleteMessages,
  getConversations,
  getUserId,
} from "../utils/storage";
import { apiClient } from "../config/api";
import type { Conversation } from "../types/conversation";

export function ChatInterface() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { agents } = useAgents();
  const agent = agents.find((a) => a.id === agentId);
  const [sessionId, setSessionId] = useState<string | undefined>();

  // Debug: Log agent data to help diagnose alias issue
  useEffect(() => {
    if (agent) {
      console.log("Current agent data:", agent);
      console.log("Agent alias:", agent.alias);
      console.log("Agent name:", agent.name);
    }
  }, [agent]);
  const { conversations, add, update, remove, refresh } =
    useConversations(agentId);
  const {
    messages,
    sendMessage,
    clearMessages,
    loadMessages,
    isStreaming,
    error,
    sessionId: streamSessionId,
  } = useStreaming(agentId || "", sessionId);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isDebugModalOpen, setIsDebugModalOpen] = useState(false);
  const [isPageIndexModalOpen, setIsPageIndexModalOpen] = useState(false);
  const [hasPageIndexAction, setHasPageIndexAction] = useState(false);

  const handleMobileMenuClose = useCallback(() => {
    setIsMobileMenuOpen(false);
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
        setHasPageIndexAction(has);
      })
      .catch(() => setHasPageIndexAction(false));
  }, [agentId]);

  // Refresh conversations when agent changes or on mount
  useEffect(() => {
    refresh();
  }, [agentId, refresh]);

  // Also refresh after sending a message to pick up any new conversations
  // This is handled in handleSendMessage, but we can also add a periodic refresh
  // that's less aggressive - only when the window is focused
  useEffect(() => {
    const handleFocus = () => {
      refresh();
    };

    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [refresh]);

  useEffect(() => {
    if (!agentId) {
      navigate("/agents");
      return;
    }
    if (agents.length > 0 && !agent) {
      navigate("/agents");
    }
  }, [agentId, agents, agent, navigate]);

  // Load messages when sessionId changes (from stream or selection)
  // Use a ref to track previous streamSessionId to prevent loops
  const prevStreamSessionIdRef = useRef<string | undefined>(streamSessionId);
  useEffect(() => {
    // Only update if streamSessionId actually changed
    if (
      streamSessionId &&
      streamSessionId !== prevStreamSessionIdRef.current &&
      streamSessionId !== sessionId
    ) {
      prevStreamSessionIdRef.current = streamSessionId;
      setSessionId(streamSessionId);
    } else if (streamSessionId) {
      prevStreamSessionIdRef.current = streamSessionId;
    }
  }, [streamSessionId, sessionId]);

  // Track previous sessionId to detect changes
  const prevSessionIdRef = useRef<string | undefined>(sessionId);

  // Load messages when sessionId changes
  useEffect(() => {
    // Only load if sessionId actually changed
    if (sessionId !== prevSessionIdRef.current) {
      const newSessionId = sessionId;
      const oldSessionId = prevSessionIdRef.current;

      console.log(
        `Switching conversation: ${oldSessionId || "none"} -> ${newSessionId || "none"}`,
      );

      prevSessionIdRef.current = sessionId;

      if (newSessionId) {
        // CRITICAL: Clear messages first to prevent showing old messages from previous session
        // This ensures no message duplication or cross-contamination
        clearMessages();

        // Load messages for the NEW session after a brief delay
        // This ensures clearMessages has completed and prevents cross-session contamination
        const timer = setTimeout(() => {
          // Double-check sessionId hasn't changed during the delay
          // This prevents loading messages for a session that's no longer active
          if (prevSessionIdRef.current === newSessionId) {
            // CRITICAL: Get messages ONLY for the new session_id
            // This ensures messages are isolated by session_id and prevents duplication
            const savedMessages = getMessages(newSessionId);
            console.log(
              `Loading ${savedMessages.length} messages for session ${newSessionId}`,
            );

            if (savedMessages && savedMessages.length > 0) {
              // Verify we're still on the same session before loading
              if (prevSessionIdRef.current === newSessionId) {
                // loadMessages will create a deep copy to prevent reference issues
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
        }, 50); // Slightly longer delay to ensure clearMessages completes

        return () => clearTimeout(timer);
      } else {
        // If sessionId is undefined (new conversation), clear messages
        // This ensures the WelcomeScreen is shown and no old messages leak through
        console.log("Starting new conversation - clearing messages");
        clearMessages();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]); // Only depend on sessionId - clearMessages and loadMessages are stable callbacks

  const handleSendMessage = async (content: string) => {
    if (!agent) return;

    const userId = getUserId();
    if (!userId) {
      console.error(
        "Cannot send message: no user_id available. User should be logged in.",
      );
      return;
    }

    const receivedSessionId = await sendMessage(content);

    // Update session ID if we received one from the server
    // This happens when:
    // 1. First interaction (no user_id, no session_id) → backend returns both
    // 2. New conversation (user_id only) → backend returns new session_id
    // 3. Continue conversation (both provided) → backend returns same session_id
    if (receivedSessionId) {
      // Check storage directly to see if conversation already exists
      // This ensures we don't miss conversations that were just added
      const allConversations = getConversations(userId);
      const existingConv = allConversations.find(
        (c) => c.session_id === receivedSessionId && c.agent_id === agent.id,
      );

      // Update session ID synchronously if it changed (urgent - affects chat content)
      // This must happen before any conversation list updates to prevent content flash
      const sessionIdChanged = receivedSessionId !== sessionId;
      if (sessionIdChanged) {
        setSessionId(receivedSessionId);
      }

      // Use startTransition to mark conversation list updates as non-urgent
      // This ensures chat content rendering is not blocked by sidebar updates
      if (!existingConv) {
        // New conversation - create entry with user_id
        const newConv: Conversation = {
          session_id: receivedSessionId,
          agent_id: agent.id,
          agent_name: agent.alias || agent.name || "Agent",
          created_at: new Date().toISOString(),
          last_message: content,
          last_message_at: new Date().toISOString(),
        };
        // Defer conversation list update to prevent interfering with chat content
        startTransition(() => {
          add(newConv);
          console.log(
            `Created new conversation: ${receivedSessionId} for agent ${agent.id}`,
          );
        });
      } else {
        // Existing conversation - update last message
        if (existingConv.last_message !== content) {
          // Defer conversation list update to prevent interfering with chat content
          startTransition(() => {
            update(receivedSessionId, {
              last_message: content,
              last_message_at: new Date().toISOString(),
            });
          });
        }
      }
    } else if (sessionId) {
      // Same session - just update last message if changed
      // Check storage directly to ensure we have the latest
      const allConversations = getConversations(userId);
      const currentConv = allConversations.find(
        (c) => c.session_id === sessionId && c.agent_id === agent.id,
      );
      if (currentConv && currentConv.last_message !== content) {
        // Use startTransition to mark update as non-urgent
        startTransition(() => {
          update(sessionId, {
            last_message: content,
            last_message_at: new Date().toISOString(),
          });
        });
      }
    }
  };

  const handleNewConversation = useCallback(() => {
    if (!agent) return;

    console.log("Starting new conversation");

    // For new conversations, clear the session ID and messages
    // When the first message is sent with user_id but no session_id,
    // the backend will create a new conversation and return the session_id

    // Clear session ID to undefined - this indicates we want a new conversation
    // The useEffect will handle clearing messages when sessionId changes
    setSessionId(undefined);

    // Refresh conversations to ensure list is up to date
    refresh();
  }, [agent, refresh]);

  const handleSelectConversation = useCallback(
    (selectedSessionId: string) => {
      // Only switch if it's a different conversation
      if (selectedSessionId === sessionId) {
        return;
      }

      console.log(
        `Switching conversation from ${sessionId || "none"} to ${selectedSessionId}`,
      );

      // Set the new session ID first - the useEffect will handle clearing and loading messages
      setSessionId(selectedSessionId);

      // Refresh conversations to ensure we have the latest data
      refresh();
    },
    [sessionId, refresh],
  );

  const handleDeleteConversation = async (sessionIdToDelete: string) => {
    if (!agent) return;

    // Get user_id from storage
    const userId = getUserId();
    if (!userId) {
      console.error("Cannot delete conversation: user_id not found");
      // Still remove from local storage for UI consistency
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
      return;
    }

    try {
      // Delete conversation on server (all sessions are real, no temp sessions)
      // Parameters: agentId, userId, sessionId
      await apiClient.deleteConversation(agent.id, userId, sessionIdToDelete);

      // Remove from local storage
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);

      // If we're currently viewing the deleted conversation, reset the chat area
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
    } catch (error: any) {
      console.error("Failed to delete conversation on server:", error);
      // Still remove from local storage even if server deletion fails
      // This ensures UI consistency
      remove(sessionIdToDelete);
      deleteMessages(sessionIdToDelete);
      if (sessionId === sessionIdToDelete) {
        handleNewConversation();
      }
    }
  };

  if (!agent) {
    return (
          <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 dark:border-indigo-400 mx-auto"></div>
          <p className="mt-4 text-gray-600 dark:text-gray-400">Loading agent...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        <ConversationList
          conversations={conversations}
          currentSessionId={sessionId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onDeleteConversation={handleDeleteConversation}
          isMobileMenuOpen={isMobileMenuOpen}
          onMobileMenuClose={handleMobileMenuClose}
        />

        <div className="flex-1 flex flex-col min-h-0 overflow-hidden min-w-0">
          <div className="bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-700 px-4 sm:px-6 py-3 sm:py-4 flex-shrink-0">
            <div className="flex items-center gap-2 sm:gap-4">
              {/* Mobile menu button */}
              <button
                onClick={() => setIsMobileMenuOpen(true)}
                className="md:hidden flex-shrink-0 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 transition-colors p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 touch-manipulation"
                aria-label="Open menu"
                title="Open menu"
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
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>

              {/* Back to agents button - desktop only */}
              <button
                onClick={() => navigate("/agents")}
                className="hidden md:flex flex-shrink-0 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 transition-colors p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                aria-label="Back to agents"
                title="Back to agents"
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
                    d="M10 19l-7-7m0 0l7-7m-7 7h18"
                  />
                </svg>
              </button>

              <div className="flex-1 min-w-0">
                <h1 className="text-lg sm:text-xl font-semibold text-gray-900 dark:text-gray-100 truncate">
                  {agent.alias || agent.name || "Agent"}
                </h1>
                {sessionId && (
                  <button
                    onClick={() => navigator.clipboard.writeText(sessionId)}
                    className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 mt-0.5"
                    title="Copy session ID"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                    Copy session ID
                  </button>
                )}
                {agent.description && (
                  <p className="text-xs sm:text-sm text-gray-600 dark:text-gray-400 mt-1 line-clamp-1">
                    {agent.description}
                  </p>
                )}
              </div>

              {/* Debug interactions button */}
              <button
                onClick={handleToggleDebugModal}
                className="flex-shrink-0 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 transition-colors p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                aria-label="Debug"
                title="Debug"
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
                    d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 100 4m0-4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 100 4m0-4v2m0-6V4"
                  />
                </svg>
              </button>

              {/* PageIndex document index button - only when agent has pageindex action */}
              {hasPageIndexAction && (
                <button
                  onClick={handleTogglePageIndexModal}
                  className="flex-shrink-0 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 transition-colors p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                  aria-label="Document index"
                  title="Document index"
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
                      d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                    />
                  </svg>
                </button>
              )}
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            {messages.length === 0 ? (
              <WelcomeScreen agentName={agent.alias || agent.name || "Agent"} />
            ) : (
              <MessageList
                messages={messages}
                showThinking={
                  isStreaming &&
                  !messages.some((m) => m.role === "assistant" && m.streaming)
                }
              />
            )}
          </div>

          {error && (
            <div className="flex-shrink-0 px-4 py-2 bg-red-50 dark:bg-red-900/30 border-t border-red-200 dark:border-red-800">
              <p className="text-sm text-red-800 dark:text-red-300">{error}</p>
            </div>
          )}

          <div className="flex-shrink-0">
            <MessageInput
            onSend={handleSendMessage}
            disabled={isStreaming}
            placeholder={`Message ${agent.alias || agent.name || "Agent"}...`}
          />
          </div>
        </div>

        {/* Debug Interactions Modal Dialog */}
        {isDebugModalOpen && (
          <DebugInteractions
            onClose={handleCloseDebugModal}
            isEmbedded={true}
          />
        )}

        {/* PageIndex Documents Modal Dialog */}
        {isPageIndexModalOpen && agentId && (
          <PageIndexDocumentsModal
            agentId={agentId}
            onClose={handleClosePageIndexModal}
            isEmbedded={true}
          />
        )}
      </div>
    </div>
  );
}
