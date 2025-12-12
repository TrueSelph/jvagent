import { useState, useCallback, useRef, useEffect } from 'react'
import { apiClient } from '../config/api'
import { saveMessages, getUserId, setUserId, getMessages, deleteMessages } from '../utils/storage'
import type { InteractionRequest, SSEChunk } from '../types/api'
import type { Message } from '../types/message'

export function useStreaming(agentId: string, sessionId?: string) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>(sessionId)
  const streamingMessageRef = useRef<string>('')
  const interactionIdRef = useRef<string | null>(null)

  // Update currentSessionId when sessionId prop changes
  // Use a ref to track the prop value and only update state when it actually changes
  const sessionIdRef = useRef<string | undefined>(sessionId)
  const messagesRef = useRef<Message[]>(messages)
  
  // Keep messagesRef in sync with messages
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  
  useEffect(() => {
    if (sessionId !== sessionIdRef.current) {
      const oldSessionId = sessionIdRef.current
      
      // CRITICAL: Save current messages to OLD session BEFORE updating refs
      // This ensures messages are saved to the correct session
      const currentMessages = messagesRef.current
      if (oldSessionId && currentMessages.length > 0 && !isLoadingRef.current) {
        // Save messages to the old session ID before switching
        // Use a deep copy to ensure we save the exact state at this moment
        saveMessages(oldSessionId, [...currentMessages])
      }
      
      // NOW update the session ID refs - this prevents any further saves to old session
      sessionIdRef.current = sessionId
      prevSessionIdRef.current = sessionId
      
      // Clear messages when switching sessions to prevent cross-contamination
      // Always clear when session changes (including when setting to undefined for new conversation)
      setMessages([])
      prevMessagesRef.current = []
      
      // Update state after clearing messages
      setCurrentSessionId(sessionId)
    }
  }, [sessionId])

  const sendMessage = useCallback(
    async (utterance: string): Promise<string | undefined> => {
      if (!utterance.trim() || isStreaming) return undefined

      setIsStreaming(true)
      setError(null)

      // Add user message
      const userMessage: Message = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: utterance,
        timestamp: new Date().toISOString(),
      }
      
      // CRITICAL: Get the current session ID from ref (most up-to-date)
      // This ensures we use the correct session_id even if session changed
      const activeSessionId = sessionIdRef.current
      
      setMessages((prev) => {
        const updated = [...prev, userMessage]
        // Save user message immediately using the ACTIVE session ID
        // Only save if we have a valid session ID (not undefined)
        // This ensures messages are always saved to the correct session
        if (activeSessionId) {
          saveMessages(activeSessionId, updated)
        }
        return updated
      })

      // Add placeholder for assistant message
      const assistantMessageId = `assistant-${Date.now()}`
      const assistantMessage: Message = {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        streaming: true,
      }
      setMessages((prev) => [...prev, assistantMessage])

      streamingMessageRef.current = ''

      let receivedSessionId: string | undefined = sessionIdRef.current

      try {
        // Get user_id from storage (set from login response)
        // This is the logged-in user's account ID, not a system-generated ID
        const userId = getUserId()

        if (!userId) {
          console.error('No user_id available - user should be logged in. Chat will not work correctly.')
          setError('User not authenticated. Please log in again.')
          setIsStreaming(false)
          setMessages((prev) =>
            prev.filter((msg) => msg.id !== assistantMessageId)
          )
          return undefined
        }

        // Align with jvagent's session management:
        // - If we have user_id but no session_id: send user_id only (new conversation)
        // - If we have both: send both (continue conversation)
        // Always send user_id if available (from login)
        const sessionIdToSend = currentSessionId || undefined

        const request: InteractionRequest = {
          utterance,
          channel: 'web',
          session_id: sessionIdToSend,
          user_id: userId || undefined, // Always include user_id if available (from login)
          stream: true,
        }

        await apiClient.streamInteract(
          agentId,
          request,
          (chunk: SSEChunk) => {
            // Note: We no longer capture user_id from chunks since we use the logged-in user's ID
            // The user_id is set from the login response and used from the first chat
            // If chunk provides a different user_id, log it for debugging but don't overwrite
            if (chunk.user_id && chunk.user_id !== getUserId()) {
              console.log('Backend returned user_id:', chunk.user_id, 'but using logged-in user_id:', getUserId())
            }
            
            if (chunk.type === 'start') {
              interactionIdRef.current = chunk.interaction_id || null
              if (chunk.session_id) {
                receivedSessionId = chunk.session_id
              }
            } else if (chunk.type === 'message' && chunk.message) {
              // Only process stream_chunk messages during streaming
              if (chunk.message.message_type === 'stream_chunk') {
                streamingMessageRef.current += chunk.message.content || ''
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantMessageId
                      ? {
                          ...msg,
                          content: streamingMessageRef.current,
                          streaming: true,
                        }
                      : msg
                  )
                )
              }
            } else if (chunk.type === 'final') {
              const finalContent =
                chunk.interaction?.response || streamingMessageRef.current
              const finalMessage: Message = {
                id: assistantMessageId,
                role: 'assistant',
                content: finalContent,
                timestamp: new Date().toISOString(),
                streaming: false,
                debugData: chunk, // Store full chunk for debug view
              }
              
              // CRITICAL: Determine the session ID to use for saving
              // Priority: receivedSessionId > sessionIdRef.current
              // If we received a new session_id, use it; otherwise use the current active one
              let sessionIdForSave: string | undefined
              
              if (receivedSessionId) {
                // We received a session_id from the backend - use it
                sessionIdForSave = receivedSessionId
                
                // Update refs if this is a new session_id
                if (receivedSessionId !== sessionIdRef.current) {
                  // Save current messages to old session before switching
                  const currentMessages = messagesRef.current
                  const oldSessionId = sessionIdRef.current
                  if (oldSessionId && currentMessages.length > 0) {
                    // Filter out the streaming message we're about to finalize
                    const messagesToSave = currentMessages.filter(msg => msg.id !== assistantMessageId)
                    if (messagesToSave.length > 0) {
                      saveMessages(oldSessionId, messagesToSave)
                    }
                  }
                  
                  // Now update to new session
                  sessionIdRef.current = receivedSessionId
                  prevSessionIdRef.current = receivedSessionId
                  setCurrentSessionId(receivedSessionId)
                }
              } else {
                // No new session_id received - use the current active one
                sessionIdForSave = sessionIdRef.current
              }
              
              setMessages((prev) => {
                const updated = prev.map((msg) =>
                  msg.id === assistantMessageId ? finalMessage : msg
                )
                // CRITICAL: Save messages to storage using the CORRECT session ID
                // This ensures messages are isolated by session_id
                if (sessionIdForSave) {
                  saveMessages(sessionIdForSave, updated)
                  // Update prev refs to match
                  prevMessagesRef.current = [...updated]
                  prevSessionIdRef.current = sessionIdForSave
                }
                return updated
              })
              setIsStreaming(false)
            } else if (chunk.type === 'error') {
              setError(chunk.message || 'An error occurred')
              setIsStreaming(false)
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantMessageId
                    ? { ...msg, streaming: false }
                    : msg
                )
              )
            }
          },
          (err: Error) => {
            setError(err.message)
            setIsStreaming(false)
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, streaming: false }
                  : msg
              )
            )
          }
        )
      } catch (err: any) {
        setError(err.message || 'Failed to send message')
        setIsStreaming(false)
        setMessages((prev) =>
          prev.filter((msg) => msg.id !== assistantMessageId)
        )
      }
      
      return receivedSessionId
    },
    [agentId, isStreaming]
  )

  const clearMessages = useCallback(() => {
    isLoadingRef.current = true // Prevent auto-save during clear
    setMessages([])
    streamingMessageRef.current = ''
    interactionIdRef.current = null
    // Clear previous messages ref to prevent stale saves
    prevMessagesRef.current = []
    // Reset flag after clear completes
    setTimeout(() => {
      isLoadingRef.current = false
    }, 50)
  }, [])

  // Track if we're manually loading messages to prevent auto-save interference
  const isLoadingRef = useRef(false)

  const loadMessages = useCallback((loadedMessages: Message[]) => {
    isLoadingRef.current = true
    setMessages(loadedMessages)
    // Update prevMessagesRef to match loaded messages
    prevMessagesRef.current = [...loadedMessages]
    // Update prevSessionIdRef to match current session
    prevSessionIdRef.current = currentSessionId
    // Reset flag after a brief delay to allow state to settle
    setTimeout(() => {
      isLoadingRef.current = false
    }, 100)
  }, [currentSessionId])
  
  // Save messages whenever they change (for conversation persistence)
  // But skip if we're in the middle of loading messages
  // Use a ref to track previous messages to prevent unnecessary saves and loops
  const prevMessagesRef = useRef<Message[]>([])
  const prevSessionIdRef = useRef<string | undefined>(currentSessionId)
  const isSavingRef = useRef(false)
  
  useEffect(() => {
    // Prevent saving if we're already in the middle of a save operation
    if (isSavingRef.current) {
      return
    }
    
    // Don't save if we're loading messages
    if (isLoadingRef.current) {
      return
    }
    
    // CRITICAL: Don't save if session ID doesn't match the ref (session just changed)
    // This prevents saving messages from old session to new session
    // Always use sessionIdRef.current as the source of truth for the active session
    const activeSessionId = sessionIdRef.current
    if (currentSessionId !== activeSessionId) {
      return
    }
    
    // Only save if we have a valid session ID and messages
    if (activeSessionId && messages.length > 0) {
      // Only save if messages actually changed (not just session)
      // We handle session changes separately in the sessionId effect above
      const messagesChanged = 
        prevMessagesRef.current.length !== messages.length ||
        prevMessagesRef.current.some((prevMsg, idx) => {
          const currMsg = messages[idx]
          return !currMsg || 
                 prevMsg.id !== currMsg.id ||
                 prevMsg.content !== currMsg.content ||
                 prevMsg.streaming !== currMsg.streaming
        })
      
      // CRITICAL: Double-check session ID matches before saving
      // This ensures messages are always saved to the correct session
      const sessionMatches = prevSessionIdRef.current === activeSessionId
      
      if (messagesChanged && sessionMatches) {
        isSavingRef.current = true
        
        // Create a deep copy of messages to save
        const messagesToSave = [...messages]
        prevMessagesRef.current = messagesToSave
        prevSessionIdRef.current = activeSessionId
        
        // CRITICAL: Save messages to the ACTIVE session ID
        // This ensures messages are isolated by session_id
        saveMessages(activeSessionId, messagesToSave)
        
        // Reset flag after save completes
        setTimeout(() => {
          isSavingRef.current = false
        }, 0)
      }
    } else if (messages.length === 0 && activeSessionId && currentSessionId === activeSessionId) {
      // If messages are cleared but we have a session ID, update refs
      prevMessagesRef.current = []
      prevSessionIdRef.current = activeSessionId
    } else if (!activeSessionId) {
      // If no session ID, clear the refs
      prevMessagesRef.current = []
      prevSessionIdRef.current = undefined
    }
  }, [messages, currentSessionId])

  return {
    messages,
    sendMessage,
    clearMessages,
    loadMessages,
    isStreaming,
    error,
    sessionId: currentSessionId,
  }
}

