const TOKEN_KEY = 'jvchat_token'
const CONVERSATIONS_KEY = 'jvchat_conversations'

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(TOKEN_KEY, token)
}

export function removeToken(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(TOKEN_KEY)
}

export function getConversations(): any[] {
  if (typeof window === 'undefined') return []
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return []
    const parsed = JSON.parse(data)
    return parsed.conversations || []
  } catch {
    return []
  }
}

export function saveConversations(conversations: any[]): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify({ conversations }))
  } catch (error) {
    console.error('Failed to save conversations:', error)
  }
}

export function addConversation(conversation: any): void {
  const conversations = getConversations()
  const existingIndex = conversations.findIndex(
    (c) => c.session_id === conversation.session_id
  )
  if (existingIndex >= 0) {
    conversations[existingIndex] = conversation
  } else {
    conversations.push(conversation)
  }
  saveConversations(conversations)
}

export function updateConversation(
  sessionId: string,
  updates: Partial<any>
): void {
  const conversations = getConversations()
  const index = conversations.findIndex((c) => c.session_id === sessionId)
  if (index >= 0) {
    conversations[index] = { ...conversations[index], ...updates }
    saveConversations(conversations)
  }
}

