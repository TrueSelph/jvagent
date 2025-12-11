import { useState } from 'react'
import type { Conversation } from '../types/conversation'

interface ConversationListProps {
  conversations: Conversation[]
  currentSessionId?: string
  onSelectConversation: (sessionId: string) => void
  onNewConversation: () => void
  onDeleteConversation?: (sessionId: string) => void
}

export function ConversationList({
  conversations,
  currentSessionId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
}: ConversationListProps) {
  const [isOpen, setIsOpen] = useState(true)

  return (
    <div
      className={`${
        isOpen ? 'w-64' : 'w-0'
      } border-r border-gray-200 bg-white transition-all duration-300 overflow-hidden flex flex-col`}
    >
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <h2 className="font-semibold text-gray-900">Conversations</h2>
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="text-gray-500 hover:text-gray-700"
        >
          {isOpen ? '←' : '→'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <button
          onClick={onNewConversation}
          className="w-full px-4 py-2 text-left text-indigo-600 hover:bg-indigo-50 font-medium"
        >
          + New Conversation
        </button>

        <div className="divide-y divide-gray-200">
          {conversations.map((conv) => (
            <div
              key={conv.session_id}
              className={`px-4 py-3 cursor-pointer hover:bg-gray-50 ${
                currentSessionId === conv.session_id
                  ? 'bg-indigo-50 border-l-4 border-indigo-600'
                  : ''
              }`}
              onClick={() => onSelectConversation(conv.session_id)}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {conv.last_message || 'New conversation'}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    {conv.last_message_at
                      ? new Date(conv.last_message_at).toLocaleDateString()
                      : new Date(conv.created_at).toLocaleDateString()}
                  </p>
                </div>
                {onDeleteConversation && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteConversation(conv.session_id)
                    }}
                    className="ml-2 text-gray-400 hover:text-red-600"
                  >
                    ×
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

