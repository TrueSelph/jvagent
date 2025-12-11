export interface Conversation {
  session_id: string
  agent_id: string
  agent_name: string
  created_at: string
  last_message?: string
  last_message_at?: string
}

export interface ConversationStorage {
  conversations: Conversation[]
}

