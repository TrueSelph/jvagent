import { useState, useMemo, memo, useCallback, useRef } from 'react'
import { LogOut } from 'lucide-react'
import type { Conversation } from '../types/conversation'
import { useAuth } from '../hooks/useAuth'

interface ConversationListProps {
  conversations: Conversation[]
  currentSessionId?: string
  onSelectConversation: (sessionId: string) => void
  onNewConversation: () => void
  onDeleteConversation?: (sessionId: string) => void
  isMobileMenuOpen?: boolean
  onMobileMenuClose?: () => void
  /** Desktop (md+): when false the rail is collapsed */
  desktopSidebarOpen: boolean
}

export const ConversationList = memo(function ConversationList({
  conversations,
  currentSessionId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  isMobileMenuOpen,
  onMobileMenuClose,
  desktopSidebarOpen,
}: ConversationListProps) {
  const { logout } = useAuth()
  const [sessionIdToDelete, setSessionIdToDelete] = useState<string | null>(null)

  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  const shouldShow = isMobile ? isMobileMenuOpen : desktopSidebarOpen

  const handleCloseOverlay = () => {
    if (isMobile && onMobileMenuClose) {
      onMobileMenuClose()
    }
  }

  const handleDeleteClick = useCallback((e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation()
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
    if (sessionId === currentSessionId) return
    onSelectConversation(sessionId)
    if (isMobile && onMobileMenuClose) {
      onMobileMenuClose()
    }
  }, [currentSessionId, onSelectConversation, isMobile, onMobileMenuClose])

  const prevConversationsRef = useRef<Conversation[]>([])
  const prevConversationsHashRef = useRef<string>('')

  const createConversationsHash = useCallback((convs: Conversation[]) => {
    return convs.map(c => `${c.session_id}:${c.last_message_at || c.created_at}:${c.last_message || ''}`).join('|')
  }, [])

  const sortedConversations = useMemo(() => {
    const currentHash = createConversationsHash(conversations)
    if (currentHash === prevConversationsHashRef.current &&
        prevConversationsRef.current.length === conversations.length &&
        conversations.length > 0) {
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
    const sorted = [...conversations].sort((a, b) => {
      const aTime = a.last_message_at || a.created_at
      const bTime = b.last_message_at || b.created_at
      const timeDiff = new Date(bTime).getTime() - new Date(aTime).getTime()
      if (timeDiff === 0) {
        return a.session_id.localeCompare(b.session_id)
      }
      return timeDiff
    })
    prevConversationsRef.current = sorted
    return sorted
  }, [conversations, createConversationsHash])

  const activeSessionIdMap = useMemo(() => {
    const map = new Map<string, boolean>()
    sortedConversations.forEach(conv => {
      map.set(conv.session_id, conv.session_id === currentSessionId)
    })
    return map
  }, [sortedConversations, currentSessionId])

  return (
    <>
      {isMobile && shouldShow && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={handleCloseOverlay}
        />
      )}

      <div
        className={`${
          shouldShow
            ? isMobile
              ? 'fixed inset-y-0 left-0 z-50 flex w-80 flex-col md:relative md:z-auto'
              : 'flex h-full min-h-0 w-64 shrink-0 flex-col md:w-72'
            : isMobile
            ? 'hidden'
            : 'w-0 shrink-0 overflow-hidden'
        } border-r border-zinc-200 bg-white transition-all duration-300 dark:border-white/10 dark:bg-zinc-900`}
      >
        <div className="flex h-[4.75rem] shrink-0 flex-col items-center justify-center border-b border-zinc-200 px-4 text-center dark:border-white/10">
          <div className="text-sm font-semibold leading-tight text-zinc-900 dark:text-zinc-50">jvchat</div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
            Agent testing
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <button
            onClick={() => {
              onNewConversation()
              if (isMobile && onMobileMenuClose) {
                onMobileMenuClose()
              }
            }}
            className="w-full px-4 py-2.5 text-left text-sm text-zinc-600 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 font-medium border-b border-zinc-200 dark:border-white/10 flex-shrink-0 touch-manipulation flex items-center gap-2 transition-colors duration-150"
          >
            <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Conversation
          </button>

          <div className="flex flex-col gap-0.5 px-2 py-2">
            {sortedConversations.length === 0 ? (
              <div className="px-4 py-8 text-center text-zinc-400 dark:text-zinc-500 text-xs">
                No conversations yet.
              </div>
            ) : (
              <>
                <div className="px-3 py-1.5 text-xs text-zinc-400 dark:text-zinc-500">
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

        <div className="flex shrink-0 border-t border-zinc-200 px-4 py-4 dark:border-white/10">
          <button
            type="button"
            onClick={() => {
              logout()
            }}
            className="flex w-full items-center justify-center gap-2 rounded-lg px-2 py-2.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800/80"
          >
            <LogOut className="size-4 shrink-0" strokeWidth={1.75} />
            Logout
          </button>
        </div>
      </div>

      {sessionIdToDelete && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50">
          <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-white/10 max-w-sm w-full p-5">
            <h3 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50 mb-2">Delete conversation?</h3>
            <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">This cannot be undone.</p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={handleDeleteCancel}
                className="px-4 py-2 text-sm font-medium text-zinc-600 dark:text-zinc-300 bg-zinc-100 dark:bg-zinc-800 rounded-lg hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors duration-150"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-600 transition-colors duration-150"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}, (prevProps, nextProps) => {
  if (prevProps.currentSessionId !== nextProps.currentSessionId) return false
  if (prevProps.isMobileMenuOpen !== nextProps.isMobileMenuOpen) return false
  if (prevProps.desktopSidebarOpen !== nextProps.desktopSidebarOpen) return false
  if (prevProps.conversations.length !== nextProps.conversations.length) return false
  const prevMap = new Map(prevProps.conversations.map(c => [c.session_id, c]))
  const nextMap = new Map(nextProps.conversations.map(c => [c.session_id, c]))
  if (prevMap.size !== nextMap.size) return false
  for (const [sessionId, prevConv] of prevMap) {
    const nextConv = nextMap.get(sessionId)
    if (!nextConv) return false
    if (
      prevConv.last_message !== nextConv.last_message ||
      prevConv.last_message_at !== nextConv.last_message_at ||
      prevConv.created_at !== nextConv.created_at
    ) {
      return false
    }
  }
  const callbacksEqual =
    prevProps.onSelectConversation === nextProps.onSelectConversation &&
    prevProps.onNewConversation === nextProps.onNewConversation &&
    prevProps.onDeleteConversation === nextProps.onDeleteConversation &&
    prevProps.onMobileMenuClose === nextProps.onMobileMenuClose
  return callbacksEqual
})

const ConversationItem = memo(({
  conversation,
  isActive,
  onSelect,
  onDelete,
}: {
  conversation: Conversation
  isActive: boolean
  onSelect: (sessionId: string) => void
  onDelete?: (e: React.MouseEvent, sessionId: string) => void
}) => {
  const handleClick = useCallback(() => {
    onSelect(conversation.session_id)
  }, [onSelect, conversation.session_id])

  const handleDelete = useCallback((e: React.MouseEvent) => {
    onDelete?.(e, conversation.session_id)
  }, [onDelete, conversation.session_id])

  return (
    <div
      className={`group flex h-9 items-center gap-2 rounded-lg px-3 transition-colors cursor-pointer ${
        isActive
          ? 'bg-zinc-100 dark:bg-zinc-800'
          : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/50'
      }`}
      onClick={handleClick}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-700 dark:text-zinc-300 truncate">
          {conversation.last_message || 'New conversation'}
        </p>
      </div>
      {onDelete && (
        <button
          onClick={handleDelete}
          className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-red-500 dark:text-zinc-500 dark:hover:text-red-400 flex-shrink-0 p-1 touch-manipulation transition-all duration-150"
          aria-label="Delete conversation"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </button>
      )}
    </div>
  )
}, (prevProps, nextProps) => {
  const conversationEqual =
    prevProps.conversation.session_id === nextProps.conversation.session_id &&
    prevProps.conversation.last_message === nextProps.conversation.last_message &&
    prevProps.conversation.last_message_at === nextProps.conversation.last_message_at &&
    prevProps.conversation.created_at === nextProps.conversation.created_at &&
    prevProps.conversation.agent_id === nextProps.conversation.agent_id &&
    prevProps.conversation.agent_name === nextProps.conversation.agent_name
  const activeEqual = prevProps.isActive === nextProps.isActive
  const callbacksEqual = prevProps.onSelect === nextProps.onSelect && prevProps.onDelete === nextProps.onDelete
  return conversationEqual && activeEqual && callbacksEqual
})

ConversationItem.displayName = 'ConversationItem'
