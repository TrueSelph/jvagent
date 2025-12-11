export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  streaming?: boolean
}

export interface StreamingMessage {
  id: string
  content: string
  complete: boolean
}

