export interface LoginRequest {
  email: string
  password: string
  serverUrl?: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  refresh_token?: string
  refresh_expires_in?: number
  user: {
    id: string
    email: string
    name: string
    created_at: string
    is_active: boolean
  }
}

export interface TokenRefreshRequest {
  refresh_token: string
}

export type TokenRefreshResponse = LoginResponse

export interface Agent {
  id: string
  namespace: string
  name: string
  alias?: string
  enabled: boolean
  description?: string
  interaction_limit?: number
}

export interface AgentsResponse {
  agents: Agent[]
  total: number
  page: number
  per_page: number
  total_pages: number
  has_previous: boolean
  has_next: boolean
  previous_page?: number
  next_page?: number
}

export interface InteractionRequest {
  utterance: string
  channel?: string
  session_id?: string
  user_id?: string
  stream?: boolean
}

export interface InteractionResponse {
  user_id: string
  session_id: string
  response?: string
  interaction: {
    id: string
    utterance: string
    response?: string
    actions: string[]
    directives: string[]
    parameters: any[]
    model_log: any[]
    messages: string[]
    streamed: boolean
  }
  report: any[]
}

export interface ResponseMessageData {
  id: string
  session_id: string
  interaction_id: string
  message_type: string
  content: string
  channel: string
  metadata: Record<string, any>
  observability_data?: Record<string, any>
  // timestamp is omitted for stream_chunk messages (not useful - chunks arrive in order,
  // timestamp only needed once when creating message bubble, client can timestamp on receipt)
  timestamp?: string | null
  // delivered is omitted from stream payloads - only meaningful for channel adapters
  // tracking external API delivery, not for direct SSE streaming
}

export interface SSEChunk {
  type: 'start' | 'message' | 'final' | 'error'
  interaction_id?: string
  session_id?: string
  user_id?: string
  message?: ResponseMessageData | string // ResponseMessageData when type === 'message', string when type === 'error'
  interaction?: InteractionResponse['interaction']
  report?: any[]
}

export interface LogEntry {
  log_id: string
  log_level: string
  status_code: number
  event_code: string
  message: string
  path: string
  method: string
  agent_id?: string
  logged_at: string
  log_data: Record<string, unknown>
}

export interface LogsPagination {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface LogsResponse {
  logs: LogEntry[]
  pagination: LogsPagination
}

