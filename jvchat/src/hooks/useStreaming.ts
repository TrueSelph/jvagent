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
      const newSessionId = sessionId
      
      console.log(`useStreaming: Session changing from ${oldSessionId || 'none'} to ${newSessionId || 'none'}`)
      
      // CRITICAL: Save current messages to OLD session BEFORE updating refs
      // This ensures messages are saved to the correct session and prevents loss
      const currentMessages = messagesRef.current
      if (oldSessionId && currentMessages.length > 0 && !isLoadingRef.current) {
        // Create a deep copy to ensure we save the exact state at this moment
        // This prevents any reference issues or duplication
        const messagesToSave = currentMessages.map(msg => ({ ...msg }))
        saveMessages(oldSessionId, messagesToSave)
        console.log(`Saved ${messagesToSave.length} messages to old session ${oldSessionId} before switching`)
      }
      
      // NOW update the session ID refs - this prevents any further saves to old session
      sessionIdRef.current = sessionId
      prevSessionIdRef.current = sessionId
      
      // Clear messages when switching sessions to prevent cross-contamination
      // Always clear when session changes (including when setting to undefined for new conversation)
      // This ensures messages from different sessions don't mix
      setMessages([])
      prevMessagesRef.current = []
      
      // Update state after clearing messages
      setCurrentSessionId(sessionId)
      
      console.log(`useStreaming: Session updated to ${sessionId || 'none'}, messages cleared`)
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
        // CRITICAL: Use sessionIdRef.current instead of currentSessionId state
        // The ref is updated immediately when sessionId prop changes, ensuring we always
        // send the correct session_id when switching conversations
        const sessionIdToSend = sessionIdRef.current || undefined

        console.log(`Sending message with session_id: ${sessionIdToSend || 'none (new conversation)'}, user_id: ${userId}`)

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
                  console.log(`Backend returned new session_id: ${receivedSessionId} (was ${sessionIdRef.current})`)
                  // Save current messages to old session before switching
                  const currentMessages = messagesRef.current
                  const oldSessionId = sessionIdRef.current
                  if (oldSessionId && currentMessages.length > 0) {
                    // Filter out the streaming message we're about to finalize
                    const messagesToSave = currentMessages
                      .filter(msg => msg.id !== assistantMessageId)
                      .map(msg => ({ ...msg })) // Deep copy
                    if (messagesToSave.length > 0) {
                      saveMessages(oldSessionId, messagesToSave)
                      console.log(`Saved ${messagesToSave.length} messages to old session ${oldSessionId}`)
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
                // This ensures messages are isolated by session_id and prevents duplication
                if (sessionIdForSave) {
                  // Create a deep copy to prevent reference issues
                  const messagesToSave = updated.map(msg => ({ ...msg }))
                  saveMessages(sessionIdForSave, messagesToSave)
                  console.log(`Saved ${messagesToSave.length} messages (including final) to session ${sessionIdForSave}`)
                  // Update prev refs to match
                  prevMessagesRef.current = messagesToSave
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
    // CRITICAL: Only load messages if we're still on the same session
    // This prevents loading messages for a session that was just switched away from
    const activeSessionId = sessionIdRef.current
    if (!activeSessionId) {
      console.warn('Cannot load messages: no active session ID')
      return
    }
    
    console.log(`Loading ${loadedMessages.length} messages for session ${activeSessionId}`)
    
    isLoadingRef.current = true
    
    // Create a deep copy to prevent reference issues and ensure isolation
    const messagesToLoad = loadedMessages.map(msg => ({ ...msg }))
    
    setMessages(messagesToLoad)
    // Update prevMessagesRef to match loaded messages
    prevMessagesRef.current = [...messagesToLoad]
    // Update prevSessionIdRef to match current session (use ref, not state)
    prevSessionIdRef.current = activeSessionId
    // Reset flag after a brief delay to allow state to settle
    setTimeout(() => {
      isLoadingRef.current = false
    }, 100)
  }, [])
  
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
    
    // CRITICAL: Always use sessionIdRef.current as the source of truth for the active session
    // This ensures we're always saving to the correct session, even if state hasn't updated yet
    const activeSessionId = sessionIdRef.current
    
    // Only save if we have a valid session ID and messages
    if (activeSessionId && messages.length > 0) {
      // CRITICAL: Double-check session ID matches before saving
      // This ensures messages are always saved to the correct session and prevents cross-contamination
      const sessionMatches = prevSessionIdRef.current === activeSessionId
      
      if (!sessionMatches) {
        // Session changed - don't save to old session
        console.log(`Session mismatch: prev=${prevSessionIdRef.current}, active=${activeSessionId} - skipping save`)
        return
      }
      
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
      
      if (messagesChanged) {
        isSavingRef.current = true
        
        // Create a deep copy of messages to save - this prevents reference issues
        // and ensures each session has its own isolated copy
        const messagesToSave = messages.map(msg => ({ ...msg }))
        prevMessagesRef.current = messagesToSave
        prevSessionIdRef.current = activeSessionId
        
        // CRITICAL: Save messages to the ACTIVE session ID
        // This ensures messages are isolated by session_id and prevents duplication
        saveMessages(activeSessionId, messagesToSave)
        console.log(`Saved ${messagesToSave.length} messages to session ${activeSessionId}`)
        
        // Reset flag after save completes
        setTimeout(() => {
          isSavingRef.current = false
        }, 0)
      }
    } else if (messages.length === 0 && activeSessionId) {
      // If messages are cleared but we have a session ID, update refs
      prevMessagesRef.current = []
      prevSessionIdRef.current = activeSessionId
    } else if (!activeSessionId) {
      // If no session ID, clear the refs
      prevMessagesRef.current = []
      prevSessionIdRef.current = undefined
    }
  }, [messages])

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

