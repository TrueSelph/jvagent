import { useState, useEffect, useCallback, useRef } from 'react'
import {
  getConversations,
  addConversation,
  updateConversation,
  removeConversation,
  syncUserIdFromAccessToken,
  getEffectiveUserId,
} from '../utils/storage'
import type { Conversation } from '../types/conversation'

export function useConversations(agentId?: string) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const stableConversationsRef = useRef<Conversation[]>([])

  // Function to refresh conversations from storage
  const refreshConversations = useCallback(() => {
    syncUserIdFromAccessToken()
    const userId = getEffectiveUserId()

    if (!userId) {
      console.warn('No user_id available - cannot load conversations')
      setConversations([])
      stableConversationsRef.current = []
      return
    }

    // Get conversations for the current user_id
    const allConversations = getConversations(userId)

    // Filter by agent_id if provided
    const filtered = agentId
      ? allConversations.filter((c) => c.agent_id === agentId)
      : allConversations

    // Sort by last_message_at or created_at (newest first) for consistent ordering
    const sorted = [...filtered].sort((a, b) => {
      const aTime = a.last_message_at || a.created_at
      const bTime = b.last_message_at || b.created_at
      return new Date(bTime).getTime() - new Date(aTime).getTime()
    })

    console.log(`Refreshed conversations: ${sorted.length} total (filtered by agentId: ${agentId || 'none'})`)

    // Always update conversations from storage to ensure we have all conversations
    // Use a simple comparison to detect changes
    setConversations((prev) => {
      // Create a string representation of session IDs for quick comparison
      const prevSessionIds = new Set(prev.map(c => c.session_id))
      const sortedSessionIds = new Set(sorted.map(c => c.session_id))

      // If the sets are different (different session IDs), update
      if (prevSessionIds.size !== sortedSessionIds.size ||
          ![...prevSessionIds].every(id => sortedSessionIds.has(id)) ||
          ![...sortedSessionIds].every(id => prevSessionIds.has(id))) {
        console.log(`Conversation list changed: ${prevSessionIds.size} -> ${sortedSessionIds.size} conversations`)
        stableConversationsRef.current = sorted
        return sorted
      }

      // Check if any conversation's content changed
      const prevMap = new Map(prev.map(c => [c.session_id, c]))
      const hasChanged = sorted.some((newConv) => {
        const prevConv = prevMap.get(newConv.session_id)
        if (!prevConv) return true

        return (
          prevConv.last_message !== newConv.last_message ||
          prevConv.last_message_at !== newConv.last_message_at ||
          prevConv.created_at !== newConv.created_at ||
          prevConv.agent_id !== newConv.agent_id ||
          prevConv.agent_name !== newConv.agent_name
        )
      })

      if (hasChanged) {
        stableConversationsRef.current = sorted
        return sorted
      }

      // Return previous reference if nothing changed
      return prev
    })
  }, [agentId])

  useEffect(() => {
    refreshConversations()
  }, [agentId, refreshConversations])

  const add = useCallback((conversation: Conversation) => {
    const userId = getEffectiveUserId()

    if (!userId) {
      console.error('Cannot add conversation: no user_id available')
      return
    }

    if (!conversation.session_id) {
      console.error('Cannot add conversation: missing session_id')
      return
    }

    console.log(`Adding conversation ${conversation.session_id} for user ${userId}`)

    // Optimistically update the UI immediately for seamless experience
    setConversations((prev) => {
      // Check if conversation already exists
      const exists = prev.some(c => c.session_id === conversation.session_id)
      if (exists) {
        // Update existing conversation
        return prev.map(c =>
          c.session_id === conversation.session_id
            ? { ...c, ...conversation }
            : c
        )
      }

      // Add new conversation at the beginning (newest first)
      const updated = [conversation, ...prev]

      // Filter by agent_id if provided
      const filtered = agentId
        ? updated.filter((c) => c.agent_id === agentId)
        : updated

      // Sort by last_message_at or created_at (newest first)
      const sorted = [...filtered].sort((a, b) => {
        const aTime = a.last_message_at || a.created_at
        const bTime = b.last_message_at || b.created_at
        return new Date(bTime).getTime() - new Date(aTime).getTime()
      })

      stableConversationsRef.current = sorted
      return sorted
    })

    // Add to storage (synchronous, so UI is already updated)
    addConversation(conversation, userId)

    // No need to refresh - we've already updated the UI optimistically
  }, [agentId])

  const update = useCallback(
    (sessionId: string, updates: Partial<Conversation>) => {
      const userId = getEffectiveUserId()

      if (!userId) {
        console.error('Cannot update conversation: no user_id available')
        return
      }

      if (!sessionId) {
        console.error('Cannot update conversation: missing session_id')
        return
      }

      // Optimistically update the UI immediately
      setConversations((prev) => {
        const updated = prev.map(c =>
          c.session_id === sessionId
            ? { ...c, ...updates }
            : c
        )

        // Sort by last_message_at or created_at (newest first)
        const sorted = [...updated].sort((a, b) => {
          const aTime = a.last_message_at || a.created_at
          const bTime = b.last_message_at || b.created_at
          return new Date(bTime).getTime() - new Date(aTime).getTime()
        })

        stableConversationsRef.current = sorted
        return sorted
      })

      // Update in storage (synchronous, so UI is already updated)
      updateConversation(sessionId, updates, userId)

      // No need to refresh - we've already updated the UI optimistically
    },
    []
  )

  const remove = useCallback((sessionId: string) => {
    const userId = getEffectiveUserId()

    if (!userId) {
      console.error('Cannot remove conversation: no user_id available')
      return
    }

    if (!sessionId) {
      console.error('Cannot remove conversation: missing session_id')
      return
    }

    console.log(`Removing conversation ${sessionId} for user ${userId}`)

    // Optimistically remove from UI immediately
    setConversations((prev) => {
      const updated = prev.filter(c => c.session_id !== sessionId)
      stableConversationsRef.current = updated
      return updated
    })

    // Remove conversation from storage (synchronous, so UI is already updated)
    removeConversation(sessionId, userId)

    // No need to refresh - we've already updated the UI optimistically
  }, [])

  return {
    conversations,
    add,
    update,
    remove,
    refresh: refreshConversations,
  }
}

