import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../types/message'
import React from 'react'

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
              <div className="break-words text-sm sm:text-base">
                <div className="markdown-content">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      // Style code blocks
                      code: ({ node, inline, className, children, ...props }: any) => {
                        return !inline ? (
                          <pre
                            className={`overflow-x-auto rounded p-2 sm:p-3 my-2 ${
                              message.role === 'user'
                                ? 'bg-indigo-500/20 text-indigo-100'
                                : 'bg-gray-800 text-gray-100'
                            }`}
                            {...props}
                          >
                            <code className={className} {...props}>
                              {children}
                            </code>
                          </pre>
                        ) : (
                          <code
                            className={`px-1 py-0.5 rounded ${
                              message.role === 'user'
                                ? 'bg-indigo-500/30 text-indigo-100'
                                : 'bg-gray-300 text-gray-800'
                            }`}
                            {...props}
                          >
                            {children}
                          </code>
                        )
                      },
                      // Style blockquotes
                      blockquote: ({ children, ...props }: any) => (
                        <blockquote
                          className={`border-l-4 pl-3 sm:pl-4 my-2 ${
                            message.role === 'user'
                              ? 'border-indigo-300 text-indigo-100'
                              : 'border-gray-400 text-gray-700'
                          }`}
                          {...props}
                        >
                          {children}
                        </blockquote>
                      ),
                      // Style links
                      a: ({ children, ...props }: any) => (
                        <a
                          className={`underline ${
                            message.role === 'user'
                              ? 'text-indigo-200 hover:text-indigo-100'
                              : 'text-indigo-600 hover:text-indigo-700'
                          }`}
                          {...props}
                        >
                          {children}
                        </a>
                      ),
                      // Style lists
                      ul: ({ children, ...props }: any) => (
                        <ul className="list-disc pl-4 sm:pl-6 my-2" {...props}>
                          {children}
                        </ul>
                      ),
                      ol: ({ children, ...props }: any) => (
                        <ol className="list-decimal pl-4 sm:pl-6 my-2" {...props}>
                          {children}
                        </ol>
                      ),
                      // Style headings
                      h1: ({ children, ...props }: any) => (
                        <h1 className="text-lg sm:text-xl font-bold my-2" {...props}>
                          {children}
                        </h1>
                      ),
                      h2: ({ children, ...props }: any) => (
                        <h2 className="text-base sm:text-lg font-bold my-2" {...props}>
                          {children}
                        </h2>
                      ),
                      h3: ({ children, ...props }: any) => (
                        <h3 className="text-sm sm:text-base font-semibold my-2" {...props}>
                          {children}
                        </h3>
                      ),
                      // Style paragraphs - add cursor to last paragraph when streaming
                      p: ({ children, ...props }: any) => {
                        // Extract text content from children for comparison
                        const extractText = (node: any): string => {
                          if (typeof node === 'string') return node
                          if (typeof node === 'number') return String(node)
                          if (React.isValidElement(node) && node.props?.children) {
                            return extractText(node.props.children)
                          }
                          if (Array.isArray(node)) {
                            return node.map(extractText).join('')
                          }
                          return ''
                        }
                        const childrenText = extractText(children)
                        // Check if this is the last paragraph by seeing if content ends with this paragraph's text
                        const isLastParagraph = message.streaming && 
                          childrenText.trim() && 
                          message.content.trim().endsWith(childrenText.trim())
                        return (
                          <p className="my-1 sm:my-2" {...props}>
                            {children}
                            {isLastParagraph && (
                              <span className="inline-block w-0.5 sm:w-1 h-3 sm:h-4 ml-0.5 sm:ml-1 bg-current animate-pulse align-middle" />
                            )}
                          </p>
                        )
                      },
                      // Style tables
                      table: ({ children, ...props }: any) => (
                        <div className="overflow-x-auto my-2">
                          <table className="border-collapse border" {...props}>
                            {children}
                          </table>
                        </div>
                      ),
                      th: ({ children, ...props }: any) => (
                        <th
                          className={`border px-2 sm:px-4 py-1 sm:py-2 ${
                            message.role === 'user'
                              ? 'bg-indigo-500/30 border-indigo-300'
                              : 'bg-gray-300 border-gray-400'
                          }`}
                          {...props}
                        >
                          {children}
                        </th>
                      ),
                      td: ({ children, ...props }: any) => (
                        <td
                          className={`border px-2 sm:px-4 py-1 sm:py-2 ${
                            message.role === 'user'
                              ? 'border-indigo-300'
                              : 'border-gray-400'
                          }`}
                          {...props}
                        >
                          {children}
                        </td>
                      ),
                    }}
                  >
                    {message.content}
                  </ReactMarkdown>
                </div>
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
