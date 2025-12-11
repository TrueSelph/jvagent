import { useState, useEffect, useCallback } from 'react'
import {
  getConversations,
  addConversation,
  updateConversation,
  saveConversations,
} from '../utils/storage'
import type { Conversation } from '../types/conversation'

export function useConversations(agentId?: string) {
  const [conversations, setConversations] = useState<Conversation[]>([])

  useEffect(() => {
    const allConversations = getConversations()
    const filtered = agentId
      ? allConversations.filter((c) => c.agent_id === agentId)
      : allConversations
    setConversations(filtered)
  }, [agentId])

  const add = useCallback((conversation: Conversation) => {
    addConversation(conversation)
    setConversations((prev) => {
      const filtered = agentId
        ? prev.filter((c) => c.agent_id === agentId)
        : prev
      return [...filtered, conversation]
    })
  }, [agentId])

  const update = useCallback(
    (sessionId: string, updates: Partial<Conversation>) => {
      updateConversation(sessionId, updates)
      setConversations((prev) =>
        prev.map((c) =>
          c.session_id === sessionId ? { ...c, ...updates } : c
        )
      )
    },
    []
  )

  const remove = useCallback((sessionId: string) => {
    const allConversations = getConversations()
    const filtered = allConversations.filter(
      (c) => c.session_id !== sessionId
    )
    saveConversations(filtered)
    setConversations((prev) =>
      prev.filter((c) => c.session_id !== sessionId)
    )
  }, [])

  return {
    conversations,
    add,
    update,
    remove,
  }
}

