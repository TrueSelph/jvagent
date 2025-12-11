import { useState, useCallback, useRef, useEffect } from 'react'
import { apiClient } from '../config/api'
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
  useEffect(() => {
    setCurrentSessionId(sessionId)
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
      setMessages((prev) => [...prev, userMessage])

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

      let receivedSessionId: string | undefined = currentSessionId

      try {
        const request: InteractionRequest = {
          utterance,
          channel: 'web',
          session_id: currentSessionId,
          stream: true,
        }

        await apiClient.streamInteract(
          agentId,
          request,
          (chunk: SSEChunk) => {
            if (chunk.type === 'start') {
              interactionIdRef.current = chunk.interaction_id || null
              if (chunk.session_id) {
                receivedSessionId = chunk.session_id
              }
            } else if (chunk.type === 'message' && chunk.message) {
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
            } else if (chunk.type === 'final') {
              const finalContent =
                chunk.interaction?.response || streamingMessageRef.current
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantMessageId
                    ? {
                        ...msg,
                        content: finalContent,
                        streaming: false,
                      }
                    : msg
                )
              )
              setIsStreaming(false)
              if (receivedSessionId) {
                setCurrentSessionId(receivedSessionId)
              }
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
    [agentId, currentSessionId, isStreaming]
  )

  const clearMessages = useCallback(() => {
    setMessages([])
    streamingMessageRef.current = ''
    interactionIdRef.current = null
  }, [])

  return {
    messages,
    sendMessage,
    clearMessages,
    isStreaming,
    error,
    sessionId: currentSessionId,
  }
}

