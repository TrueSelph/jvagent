import { useEffect, useRef, useState } from 'react'
import type { Message } from '../types/message'

interface MessageListProps {
  messages: Message[]
  showThinking?: boolean
  thinkingText?: string
}

export function MessageList({
  messages,
  showThinking = false,
  thinkingText = 'Thinking...',
}: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const prevLenRef = useRef<number>(0)
  const [debugMessage, setDebugMessage] = useState<Message | null>(null)

  useEffect(() => {
    // Avoid smooth-scroll on every token append (prevents visible flicker).
    const behavior = messages.length > prevLenRef.current ? 'smooth' : 'auto'
    messagesEndRef.current?.scrollIntoView({ behavior })
    prevLenRef.current = messages.length
  }, [messages])

  if (messages.length === 0) {
    return null
  }

  return (
    <>
      <div className="flex-1 overflow-y-auto px-3 sm:px-4 py-4 sm:py-6 space-y-3 sm:space-y-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${
              message.role === 'user' ? 'justify-end' : 'justify-start'
            }`}
          >
            <div
              className={`max-w-[85%] sm:max-w-3xl rounded-lg px-3 sm:px-4 py-2 sm:py-3 relative ${
                message.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-200 text-gray-900'
              }`}
            >
              <div className="whitespace-pre-wrap break-words text-sm sm:text-base">
                {message.content}
                {message.streaming && (
                  <span className="inline-block w-2 h-4 ml-1 bg-current animate-pulse" />
                )}
              </div>
              <div className="flex items-center justify-between mt-1 sm:mt-2 gap-2">
                <div
                  className={`text-xs ${
                    message.role === 'user' ? 'text-indigo-200' : 'text-gray-500'
                  }`}
                >
                  {new Date(message.timestamp).toLocaleTimeString()}
                </div>
                {(() => {
                  // Show debug button if:
                  // 1. Message has debugData, OR
                  // 2. Message is the last assistant message for its interactionId
                  const shouldShowDebug = message.debugData || (
                    message.role === 'assistant' &&
                    message.interactionId &&
                    (() => {
                      const messagesForInteraction = messages.filter(
                        m => m.role === 'assistant' && m.interactionId === message.interactionId
                      )
                      const lastMessageForInteraction = messagesForInteraction[messagesForInteraction.length - 1]
                      return lastMessageForInteraction?.id === message.id
                    })()
                  )
                  
                  return shouldShowDebug ? (
                    <button
                      onClick={() => {
                        // Find the message with debugData for this interaction
                        const debugMessageForInteraction = messages.find(
                          m => m.interactionId === message.interactionId && m.debugData
                        )
                        setDebugMessage(debugMessageForInteraction || message)
                      }}
                      className={`text-xs px-2 py-1 rounded touch-manipulation ${
                        message.role === 'user'
                          ? 'bg-indigo-500 hover:bg-indigo-400 text-white'
                          : 'bg-gray-300 hover:bg-gray-400 text-gray-700'
                      }`}
                    >
                      Debug
                    </button>
                  ) : null
                })()}
              </div>
            </div>
          </div>
        ))}

        {showThinking && (
          <div className="flex justify-start">
            <div className="max-w-[85%] sm:max-w-3xl rounded-lg px-3 sm:px-4 py-2 sm:py-3 bg-gray-200 text-gray-900">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                <span className="text-xs sm:text-sm text-gray-600">
                  {thinkingText}
                </span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Debug Modal */}
      {debugMessage && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-2 sm:p-4"
          onClick={() => setDebugMessage(null)}
        >
          <div
            className="bg-white rounded-lg max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
              <h3 className="text-base sm:text-lg font-semibold text-gray-900">
                Debug View - Message {debugMessage.id.substring(0, 20)}...
              </h3>
              <button
                onClick={() => setDebugMessage(null)}
                className="text-gray-400 hover:text-gray-600 text-2xl touch-manipulation p-1"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 sm:p-6">
              <div className="mb-4">
                <h4 className="text-xs sm:text-sm font-semibold text-gray-700 mb-2">
                  Message Content:
                </h4>
                <div className="bg-gray-50 p-2 sm:p-3 rounded border border-gray-200">
                  <pre className="whitespace-pre-wrap text-xs sm:text-sm text-gray-800">
                    {debugMessage.debugData?.interaction?.response || debugMessage.content}
                  </pre>
                </div>
              </div>
              {debugMessage.debugData ? (
                <div>
                  <h4 className="text-xs sm:text-sm font-semibold text-gray-700 mb-2">
                    Full JSON Response (type=final):
                  </h4>
                  <div className="bg-gray-900 p-2 sm:p-4 rounded border border-gray-700 overflow-x-auto">
                    <pre className="text-xs text-green-400">
                      {JSON.stringify(debugMessage.debugData, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="text-xs sm:text-sm text-gray-500 italic">
                  Debug data not available yet. Waiting for final interaction data...
                </div>
              )}
            </div>
            <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-gray-200 flex justify-end flex-shrink-0">
              <button
                onClick={() => {
                  navigator.clipboard.writeText(
                    JSON.stringify(debugMessage.debugData, null, 2)
                  )
                }}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 touch-manipulation text-sm sm:text-base"
              >
                Copy JSON
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
