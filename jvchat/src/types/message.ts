export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  streaming?: boolean
  debugData?: any // Store final message JSON for debug view
  interactionId?: string // Used to prevent cross-interaction overwrites
  /** Echo of server ResponseMessage.metadata (e.g. media_url, media_type) */
  metadata?: Record<string, unknown>
  /** Logical response stream category. */
  category?: 'user' | 'thought'
  /** Thought subtype when category === "thought". */
  thoughtType?: 'reasoning' | 'tool_call' | 'tool_result' | 'status'
  /** Segment key grouping thought stream chunks. */
  segmentId?: string
}

export interface StreamingMessage {
  id: string
  content: string
  complete: boolean
}

