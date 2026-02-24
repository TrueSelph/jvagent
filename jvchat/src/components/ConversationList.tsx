import { useState, useEffect, useMemo, memo, useCallback, useRef } from 'react'
import type { Conversation } from '../types/conversation'

interface ConversationListProps {
  conversations: Conversation[]
  currentSessionId?: string
  onSelectConversation: (sessionId: string) => void
  onNewConversation: () => void
  onDeleteConversation?: (sessionId: string) => void
  isMobileMenuOpen?: boolean
  onMobileMenuClose?: () => void
}

export const ConversationList = memo(function ConversationList({
  conversations,
  currentSessionId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  isMobileMenuOpen,
  onMobileMenuClose,
}: ConversationListProps) {
  const [isOpen, setIsOpen] = useState(true)
  const [sessionIdToDelete, setSessionIdToDelete] = useState<string | null>(null)

  // On mobile, start with sidebar closed
  useEffect(() => {
    const checkMobile = () => {
      if (window.innerWidth < 768) {
        setIsOpen(false)
      } else {
        setIsOpen(true)
      }
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // Handle mobile menu state
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  const shouldShow = isMobile ? isMobileMenuOpen : isOpen

  const handleClose = () => {
    if (isMobile && onMobileMenuClose) {
      onMobileMenuClose()
    } else {
      setIsOpen(false)
    }
  }

  const handleDeleteClick = useCallback((sessionId: string) => {
    setSessionIdToDelete(sessionId)
  }, [])

  const handleDeleteConfirm = useCallback(() => {
    if (sessionIdToDelete && onDeleteConversation) {
      onDeleteConversation(sessionIdToDelete)
      setSessionIdToDelete(null)
      if (isMobile && onMobileMenuClose) {
        onMobileMenuClose()
      }
    }
  }, [sessionIdToDelete, onDeleteConversation, isMobile, onMobileMenuClose])

  const handleDeleteCancel = useCallback(() => {
    setSessionIdToDelete(null)
  }, [])

  const handleSelect = useCallback((sessionId: string) => {
    // Prevent selecting the same conversation
    if (sessionId === currentSessionId) {
      return
    }
    onSelectConversation(sessionId)
    // Close mobile menu after selection
    if (isMobile && onMobileMenuClose) {
      onMobileMenuClose()
    }
  }, [currentSessionId, onSelectConversation, isMobile, onMobileMenuClose])

  // Track previous conversations to detect actual changes
  const prevConversationsRef = useRef<Conversation[]>([])
  const prevConversationsHashRef = useRef<string>('')

  // Create a stable hash for conversations comparison
  const createConversationsHash = useCallback((convs: Conversation[]) => {
    return convs.map(c => `${c.session_id}:${c.last_message_at || c.created_at}:${c.last_message || ''}`).join('|')
  }, [])

  // Memoize sorted conversations with deep comparison to prevent unnecessary re-renders
  const sortedConversations = useMemo(() => {
    const currentHash = createConversationsHash(conversations)

    // Only re-sort if conversations actually changed
    if (currentHash === prevConversationsHashRef.current &&
        prevConversationsRef.current.length === conversations.length &&
        conversations.length > 0) {
      // Verify the arrays are actually the same
      const isSame = conversations.every((conv, idx) => {
        const prevConv = prevConversationsRef.current[idx]
        return prevConv && prevConv.session_id === conv.session_id
      })
      if (isSame) {
        return prevConversationsRef.current
      }
    }

    prevConversationsHashRef.current = currentHash

    if (conversations.length === 0) {
      prevConversationsRef.current = []
      return []
    }

    // Create a stable sorted array
    const sorted = [...conversations].sort((a, b) => {
      const aTime = a.last_message_at || a.created_at
      const bTime = b.last_message_at || b.created_at
      const timeDiff = new Date(bTime).getTime() - new Date(aTime).getTime()
      // If times are equal, use session_id for stable sorting
      if (timeDiff === 0) {
        return a.session_id.localeCompare(b.session_id)
      }
      return timeDiff
    })

    prevConversationsRef.current = sorted
    return sorted
  }, [conversations, createConversationsHash])

  // Create a stable map of active states to prevent unnecessary re-renders
  const activeSessionIdMap = useMemo(() => {
    const map = new Map<string, boolean>()
    sortedConversations.forEach(conv => {
      map.set(conv.session_id, conv.session_id === currentSessionId)
    })
    return map
  }, [sortedConversations, currentSessionId])

  return (
    <>
      {/* Mobile overlay */}
      {isMobile && shouldShow && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 z-40 md:hidden"
          onClick={handleClose}
        />
      )}

      {/* Collapsed sidebar button - desktop only */}
      {!isMobile && !isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed left-0 top-1/2 -translate-y-1/2 z-10 bg-white dark:bg-slate-900 border-r border-y border-gray-200 dark:border-slate-700 rounded-r-lg px-2 py-4 text-gray-500 hover:text-gray-700 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-gray-50 dark:hover:bg-slate-800 transition-colors hidden md:block"
          aria-label="Show sidebar"
        >
          →
        </button>
      )}

      <div
        className={`${
          shouldShow
            ? isMobile
              ? 'fixed inset-y-0 left-0 w-80 z-50 md:relative md:z-auto'
              : 'w-64 md:w-80'
            : isMobile
            ? 'hidden'
            : 'w-0'
        } border-r border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 transition-all duration-300 overflow-hidden flex flex-col shadow-lg md:shadow-none`}
      >
        <div className="px-4 sm:px-6 py-4 border-b border-gray-200 dark:border-slate-700 flex items-center justify-between flex-shrink-0">
          <h2 className="font-semibold text-gray-900 dark:text-gray-100">Conversations</h2>
          <button
            onClick={handleClose}
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 p-2 -mr-2 touch-manipulation"
            aria-label="Close sidebar"
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
        </div>

      <div className="flex-1 overflow-y-auto">
        <button
          onClick={() => {
            onNewConversation()
            if (isMobile && onMobileMenuClose) {
              onMobileMenuClose()
            }
          }}
          className="w-full px-4 sm:px-6 py-3 text-left text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 font-medium border-b border-gray-200 dark:border-slate-700 flex-shrink-0 touch-manipulation flex items-center gap-2"
        >
          <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New Conversation
        </button>

        <div className="divide-y divide-gray-200 dark:divide-slate-700">
          {sortedConversations.length === 0 ? (
            <div className="px-4 sm:px-6 py-8 text-center text-gray-500 dark:text-gray-400 text-sm">
              No conversations yet. Start a new conversation to begin.
            </div>
          ) : (
            <>
              <div className="px-4 sm:px-6 py-2 text-xs text-gray-500 dark:text-slate-400 border-b border-gray-200 dark:border-slate-700">
                {sortedConversations.length} conversation{sortedConversations.length !== 1 ? 's' : ''}
              </div>
              {sortedConversations.map((conv) => (
                <ConversationItem
                  key={conv.session_id}
                  conversation={conv}
                  isActive={activeSessionIdMap.get(conv.session_id) || false}
                  onSelect={handleSelect}
                  onDelete={onDeleteConversation ? handleDeleteClick : undefined}
                />
              ))}
            </>
          )}
        </div>
      </div>

      {sessionIdToDelete && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 dark:bg-black/70">
          <div className="bg-white dark:bg-slate-900 rounded-lg shadow-xl max-w-sm w-full p-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">Delete conversation?</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">This cannot be undone.</p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={handleDeleteCancel}
                className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-600"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </>
  )
}, (prevProps, nextProps) => {
  // Custom comparison function for ConversationList memo
  // Return true if props are equal (should NOT re-render)

  // Quick checks first
  if (prevProps.currentSessionId !== nextProps.currentSessionId) {
    return false
  }

  if (prevProps.isMobileMenuOpen !== nextProps.isMobileMenuOpen) {
    return false
  }

  if (prevProps.conversations.length !== nextProps.conversations.length) {
    return false
  }

  // Create maps for efficient comparison
  const prevMap = new Map(prevProps.conversations.map(c => [c.session_id, c]))
  const nextMap = new Map(nextProps.conversations.map(c => [c.session_id, c]))

  // Check if all conversations are the same
  if (prevMap.size !== nextMap.size) {
    return false
  }

  // Compare each conversation
  for (const [sessionId, prevConv] of prevMap) {
    const nextConv = nextMap.get(sessionId)
    if (!nextConv) {
      return false
    }
    // Only compare fields that affect rendering
    if (
      prevConv.last_message !== nextConv.last_message ||
      prevConv.last_message_at !== nextConv.last_message_at ||
      prevConv.created_at !== nextConv.created_at
    ) {
      return false
    }
  }

  // Callbacks should be stable, but check them anyway
  const callbacksEqual =
    prevProps.onSelectConversation === nextProps.onSelectConversation &&
    prevProps.onNewConversation === nextProps.onNewConversation &&
    prevProps.onDeleteConversation === nextProps.onDeleteConversation &&
    prevProps.onMobileMenuClose === nextProps.onMobileMenuClose

  // Return true if all props are equal (no re-render needed)
  return callbacksEqual
})

// Memoized conversation item to prevent unnecessary re-renders
const ConversationItem = memo(({
  conversation,
  isActive,
  onSelect,
  onDelete,
}: {
  conversation: Conversation
  isActive: boolean
  onSelect: (sessionId: string) => void
  onDelete?: (sessionId: string) => void
}) => {
  const handleClick = useCallback(() => {
    onSelect(conversation.session_id)
  }, [onSelect, conversation.session_id])

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onDelete?.(conversation.session_id)
  }, [onDelete, conversation.session_id])

  return (
    <div
      className={`px-4 sm:px-6 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 border-l-4 transition-colors duration-150 ${
        isActive
          ? 'bg-indigo-50 dark:bg-indigo-900/30 border-indigo-600 dark:border-indigo-500'
          : 'border-transparent'
      }`}
      onClick={handleClick}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0 pr-2">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
            {conversation.last_message || 'New conversation'}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            {conversation.last_message_at
              ? new Date(conversation.last_message_at).toLocaleDateString()
              : new Date(conversation.created_at).toLocaleDateString()}
          </p>
        </div>
        {onDelete && (
          <button
            onClick={handleDelete}
            className="ml-2 text-gray-400 hover:text-red-600 dark:text-gray-500 dark:hover:text-red-400 flex-shrink-0 p-1 touch-manipulation"
            aria-label="Delete conversation"
          >
            ×
          </button>
        )}
      </div>
    </div>
  )
}, (prevProps, nextProps) => {
  // Custom comparison function for memo - return true if props are equal (should NOT re-render)
  const conversationEqual =
    prevProps.conversation.session_id === nextProps.conversation.session_id &&
    prevProps.conversation.last_message === nextProps.conversation.last_message &&
    prevProps.conversation.last_message_at === nextProps.conversation.last_message_at &&
    prevProps.conversation.created_at === nextProps.conversation.created_at &&
    prevProps.conversation.agent_id === nextProps.conversation.agent_id &&
    prevProps.conversation.agent_name === nextProps.conversation.agent_name

  const activeEqual = prevProps.isActive === nextProps.isActive
  const callbacksEqual = prevProps.onSelect === nextProps.onSelect && prevProps.onDelete === nextProps.onDelete

  // Return true if all props are equal (no re-render needed)
  return conversationEqual && activeEqual && callbacksEqual
})

ConversationItem.displayName = 'ConversationItem'

