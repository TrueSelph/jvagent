import { useEffect, useRef } from 'react'
import type { Message } from '../types/message'

interface MessageListProps {
  messages: Message[]
}

export function MessageList({ messages }: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return null
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
      {messages.map((message) => (
        <div
          key={message.id}
          className={`flex ${
            message.role === 'user' ? 'justify-end' : 'justify-start'
          }`}
        >
          <div
            className={`max-w-3xl rounded-lg px-4 py-2 ${
              message.role === 'user'
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-200 text-gray-900'
            }`}
          >
            <div className="whitespace-pre-wrap break-words">
              {message.content}
              {message.streaming && (
                <span className="inline-block w-2 h-4 ml-1 bg-current animate-pulse" />
              )}
            </div>
            <div
              className={`text-xs mt-1 ${
                message.role === 'user'
                  ? 'text-indigo-200'
                  : 'text-gray-500'
              }`}
            >
              {new Date(message.timestamp).toLocaleTimeString()}
            </div>
          </div>
        </div>
      ))}
      <div ref={messagesEndRef} />
    </div>
  )
}

