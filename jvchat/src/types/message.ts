export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  streaming?: boolean
  debugData?: any // Store final message JSON for debug view
}

export interface StreamingMessage {
  id: string
  content: string
  complete: boolean
}

