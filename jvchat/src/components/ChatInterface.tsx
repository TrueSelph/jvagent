import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useAgents } from '../hooks/useAgents'
import { useStreaming } from '../hooks/useStreaming'
import { useConversations } from '../hooks/useConversations'
import { MessageList } from './MessageList'
import { MessageInput } from './MessageInput'
import { WelcomeScreen } from './WelcomeScreen'
import { ConversationList } from './ConversationList'
import { addConversation, updateConversation } from '../utils/storage'
import type { Conversation } from '../types/conversation'

export function ChatInterface() {
  const { agentId } = useParams<{ agentId: string }>()
  const navigate = useNavigate()
  const { agents } = useAgents()
  const agent = agents.find((a) => a.id === agentId)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const { conversations, add, update, remove } = useConversations(agentId)
  const { messages, sendMessage, clearMessages, isStreaming, error, sessionId: streamSessionId } =
    useStreaming(agentId || '', sessionId)

  useEffect(() => {
    if (!agentId) {
      navigate('/agents')
      return
    }
    if (agents.length > 0 && !agent) {
      navigate('/agents')
    }
  }, [agentId, agents, agent, navigate])

  const handleSendMessage = async (content: string) => {
    if (!agent) return

    const receivedSessionId = await sendMessage(content)

    // Update session ID if we received one from the server
    if (receivedSessionId && receivedSessionId !== sessionId) {
      setSessionId(receivedSessionId)
      
      // Create or update conversation
      const existingConv = conversations.find(c => c.session_id === receivedSessionId)
      if (!existingConv) {
        const newConv: Conversation = {
          session_id: receivedSessionId,
          agent_id: agent.id,
          agent_name: agent.alias || agent.name,
          created_at: new Date().toISOString(),
          last_message: content,
          last_message_at: new Date().toISOString(),
        }
        add(newConv)
      } else {
        update(receivedSessionId, {
          last_message: content,
          last_message_at: new Date().toISOString(),
        })
      }
    } else if (sessionId) {
      update(sessionId, {
        last_message: content,
        last_message_at: new Date().toISOString(),
      })
    }
  }

  const handleNewConversation = () => {
    setSessionId(undefined)
    clearMessages()
  }

  const handleSelectConversation = (selectedSessionId: string) => {
    setSessionId(selectedSessionId)
    clearMessages()
    // TODO: Load conversation history from API
  }

  const handleDeleteConversation = (sessionIdToDelete: string) => {
    remove(sessionIdToDelete)
    if (sessionId === sessionIdToDelete) {
      handleNewConversation()
    }
  }

  if (!agent) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading agent...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-gray-50">
      <div className="flex flex-1 overflow-hidden">
        <ConversationList
          conversations={conversations}
          currentSessionId={sessionId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onDeleteConversation={handleDeleteConversation}
        />

        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="bg-white border-b border-gray-200 px-6 py-4">
            <h1 className="text-xl font-semibold text-gray-900">
              {agent.alias || agent.name}
            </h1>
            {agent.description && (
              <p className="text-sm text-gray-600 mt-1">{agent.description}</p>
            )}
          </div>

          {messages.length === 0 ? (
            <WelcomeScreen agentName={agent.alias || agent.name} />
          ) : (
            <MessageList messages={messages} />
          )}

          {error && (
            <div className="px-4 py-2 bg-red-50 border-t border-red-200">
              <p className="text-sm text-red-800">{error}</p>
            </div>
          )}

          <MessageInput
            onSend={handleSendMessage}
            disabled={isStreaming}
            placeholder={`Message ${agent.alias || agent.name}...`}
          />
        </div>
      </div>
    </div>
  )
}

