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

      // Track assistant message ID for fallback (but don't create placeholder bubble)
      // Actual message bubbles will be created when stream chunks or adhoc messages arrive
      const assistantMessageId = `assistant-${Date.now()}`

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
          setMessages((prev) => {
            // Remove placeholder bubbles if any
            return prev.filter((m) => m.id !== assistantMessageId || m.content !== '')
          })
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
            } else if (chunk.type === 'message' && chunk.message && typeof chunk.message === 'object') {
              // Type guard: chunk.message is ResponseMessageData (not string)
              const msg = chunk.message
              // Handle stream_chunk messages during streaming
              // Group by message.id (sequence identifier) - all chunks in same sequence share same id
              if (msg.message_type === 'stream_chunk') {
                const messageId = msg.id || assistantMessageId // Fallback to assistantMessageId if id missing
                setMessages((prev) => {
                  // Remove any placeholder bubbles with assistantMessageId when first real message arrives
                  const filtered = prev.filter((m) => m.id !== assistantMessageId || m.content !== '')
                  
                  // Find existing message with this id, or create new one
                  const existingIndex = filtered.findIndex((m) => m.id === messageId)
                  if (existingIndex >= 0) {
                    // Update existing message - accumulate content
                    const existing = filtered[existingIndex]
                    const updatedContent = (existing.content || '') + (msg.content || '')
                    return filtered.map((m, idx) =>
                      idx === existingIndex
                        ? {
                            ...m,
                            content: updatedContent,
                            streaming: true,
                            interactionId: m.interactionId || msg.interaction_id,
                          }
                        : m
                    )
                  } else {
                    // Create new message bubble for this sequence
                    const newMessage: Message = {
                      id: messageId,
                      role: 'assistant',
                      content: msg.content || '',
                      interactionId: msg.interaction_id,
                      timestamp: msg.timestamp || new Date().toISOString(),
                      streaming: true,
                      // Don't add debugData to stream chunks - only last message gets it
                    }
                    return [...filtered, newMessage]
                  }
                })
              } else if (msg.message_type === 'final') {
                // Handle final message - use message.id to find matching sequence
                // Final message is just a completion signal - NEVER update bubble content
                // Message bubbles should ONLY contain their respective message_type content (stream_chunk, adhoc)
                const messageId = msg.id || assistantMessageId // Fallback to assistantMessageId if id missing
                
                setMessages((prev) => {
                  // Find existing message with this id
                  const existingIndex = prev.findIndex((m) => m.id === messageId)
                  let updated: Message[]
                  
                  if (existingIndex >= 0) {
                    // Update existing message - ONLY stop streaming indicator, NEVER update content
                    const existing = prev[existingIndex]
                    const wasStreaming = existing.streaming
                    
                    if (wasStreaming) {
                      // Only update streaming status, preserve existing content
                      updated = prev.map((m, idx) => {
                        if (idx === existingIndex) {
                          return {
                            ...m,
                            streaming: false,
                            interactionId: m.interactionId || msg.interaction_id,
                            // Keep existing debugData if it exists, will be updated by chunk.type='final'
                          }
                        }
                        return m
                      })
                    } else {
                      // No change needed, but ensure streaming is false
                      updated = prev.map((m, idx) => {
                        if (idx === existingIndex && m.streaming) {
                          return { ...m, streaming: false }
                        }
                        return m
                      })
                    }
                  } else {
                    // Final message should not create a new bubble - bubbles are created by stream_chunk/adhoc messages
                    // Just return existing messages unchanged
                    updated = prev
                  }
                  
                  // Ensure only the last message of each interaction has debugData
                  // Group messages by interactionId and keep debugData only on the last message per interaction
                  const interactionGroups = new Map<string, number[]>()
                  updated.forEach((m, idx) => {
                    if (m.role === 'assistant' && m.interactionId) {
                      const indices = interactionGroups.get(m.interactionId) || []
                      indices.push(idx)
                      interactionGroups.set(m.interactionId, indices)
                    }
                  })
                  
                  // Remove debugData from all messages except the last one per interaction
                  updated = updated.map((m, idx) => {
                    if (m.role === 'assistant' && m.interactionId && m.debugData) {
                      const indices = interactionGroups.get(m.interactionId) || []
                      const lastIndexForInteraction = indices.length > 0 ? indices[indices.length - 1] : -1
                      if (idx !== lastIndexForInteraction) {
                        const { debugData, ...rest } = m
                        return rest
                      }
                    }
                    return m
                  })
                  
                  // Save messages if we have a session ID
                  const activeSessionId = sessionIdRef.current
                  if (activeSessionId) {
                    saveMessages(activeSessionId, updated)
                  }
                  return updated
                })
                // Don't set isStreaming to false here - wait for chunk.type='final' for complete payload
              } else if (msg.message_type === 'adhoc') {
                // Handle adhoc messages as separate message bubbles
                // Each adhoc message gets its own unique ID
                const adhocMessage: Message = {
                  id: msg.id || `adhoc-${Date.now()}-${Math.random()}`,
                  role: 'assistant',
                  interactionId: msg.interaction_id,
                  content: msg.content || '',
                  timestamp: msg.timestamp || new Date().toISOString(),
                  streaming: false,
                  // Don't add debugData to adhoc messages - only last message gets it
                }
                setMessages((prev) => {
                  // Append as new message (don't update existing messages)
                  let updated = [...prev, adhocMessage]
                  
                  // Ensure only the last message of each interaction has debugData
                  // Group messages by interactionId and keep debugData only on the last message per interaction
                  const interactionGroups = new Map<string, number[]>()
                  updated.forEach((m, idx) => {
                    if (m.role === 'assistant' && m.interactionId) {
                      const indices = interactionGroups.get(m.interactionId) || []
                      indices.push(idx)
                      interactionGroups.set(m.interactionId, indices)
                    }
                  })
                  
                  // Remove debugData from all messages except the last one per interaction
                  updated = updated.map((m, idx) => {
                    if (m.role === 'assistant' && m.interactionId && m.debugData) {
                      const indices = interactionGroups.get(m.interactionId) || []
                      const lastIndexForInteraction = indices.length > 0 ? indices[indices.length - 1] : -1
                      if (idx !== lastIndexForInteraction) {
                        const { debugData, ...rest } = m
                        return rest
                      }
                    }
                    return m
                  })
                  
                  // Save adhoc message immediately if we have a session ID
                  const activeSessionId = sessionIdRef.current
                  if (activeSessionId) {
                    saveMessages(activeSessionId, updated)
                  }
                  return updated
                })
              }
            } else if (chunk.type === 'final') {
              // Full SSE final chunk (type="final") - contains interaction and report data
              // This is ONLY for storing debugData - DO NOT update message bubble content
              // Message bubbles should only contain their respective message_type content (stream_chunk, adhoc)
              
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
                    // Filter out any placeholder messages
                    const messagesToSave = currentMessages
                      .filter(msg => msg.id !== assistantMessageId || msg.content !== '')
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
                // Remove any placeholder bubbles
                const filtered = prev.filter((m) => m.id !== assistantMessageId || m.content !== '')
                const interactionIdForFinal =
                  chunk.interaction?.id || interactionIdRef.current || undefined

                const targetIndex = interactionIdForFinal
                  ? filtered.findLastIndex(
                      (m) =>
                        m.role === 'assistant' &&
                        m.interactionId === interactionIdForFinal
                    )
                  : filtered.findLastIndex((m) => m.role === 'assistant')

                let updated: Message[]

                if (targetIndex >= 0) {
                  updated = filtered.map((m, idx) => {
                    if (idx === targetIndex) {
                      // DO NOT update content - preserve existing bubble content from stream_chunk/adhoc messages
                      // Only update streaming status and debugData
                      const wasStreaming = m.streaming === true

                      if (wasStreaming) {
                        return {
                          ...m,
                          streaming: false,
                          // Store full SSE chunk with interaction and report
                          debugData: chunk,
                          interactionId: m.interactionId || interactionIdForFinal,
                        }
                      }

                      // If not streaming, just ensure debugData is set
                      if (!m.debugData) {
                        return { ...m, debugData: chunk }
                      }
                      return m
                    }

                    // Only remove debugData if this message is not the last one for its interaction
                    if (m.role === 'assistant' && m.debugData && m.interactionId) {
                      // Check if this is the last message for this interaction
                      const messagesForInteraction = filtered.filter(
                        msg => msg.role === 'assistant' && msg.interactionId === m.interactionId
                      )
                      const lastMessageForInteraction = messagesForInteraction[messagesForInteraction.length - 1]
                      if (m.id !== lastMessageForInteraction?.id) {
                        const { debugData, ...rest } = m
                        return rest
                      }
                    }

                    return m
                  })
                } else {
                  // No bubble for this interaction - this shouldn't happen if stream_chunks were received
                  // But if it does, don't create a bubble with aggregated content
                  // Just ensure debugData is preserved if we have any assistant messages
                  updated = filtered.map((m) => {
                    if (m.role === 'assistant' && m.debugData) {
                      const { debugData, ...rest } = m
                      return rest
                    }
                    return m
                  })
                  
                  // Only create a new bubble if we have no assistant messages at all (edge case)
                  if (updated.filter(m => m.role === 'assistant').length === 0) {
                    const finalMessage: Message = {
                      id: assistantMessageId,
                      role: 'assistant',
                      content: '', // Empty - bubbles should only contain message_type content
                      timestamp: new Date().toISOString(),
                      streaming: false,
                      debugData: chunk, // Store full SSE chunk with interaction and report
                      interactionId: interactionIdForFinal,
                    }
                    updated = [...updated, finalMessage]
                  }
                }

                // CRITICAL: Save messages to storage using the CORRECT session ID
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
              setMessages((prev) => {
                // Remove placeholder bubbles and update any streaming messages
                return prev
                  .filter((m) => m.id !== assistantMessageId || m.content !== '')
                  .map((msg) =>
                    msg.streaming ? { ...msg, streaming: false } : msg
                  )
              })
            }
          },
          (err: Error) => {
            setError(err.message)
            setIsStreaming(false)
            setMessages((prev) => {
              // Remove placeholder bubbles and update any streaming messages
              return prev
                .filter((m) => m.id !== assistantMessageId || m.content !== '')
                .map((msg) =>
                  msg.streaming ? { ...msg, streaming: false } : msg
                )
            })
          }
        )
      } catch (err: any) {
        setError(err.message || 'Failed to send message')
        setIsStreaming(false)
        setMessages((prev) => {
          // Remove placeholder bubbles
          return prev.filter((m) => m.id !== assistantMessageId || m.content !== '')
        })
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

