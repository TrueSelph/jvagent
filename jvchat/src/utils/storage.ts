const TOKEN_KEY = 'jvchat_token'
const USER_ID_KEY = 'jvchat_user_id'
const CONVERSATIONS_KEY = 'jvchat_conversations'
const MESSAGES_KEY = 'jvchat_messages' // Store messages by session_id

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

export function getUserId(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(USER_ID_KEY)
}

export function setUserId(userId: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(USER_ID_KEY, userId)
}

export function removeUserId(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(USER_ID_KEY)
}

// Storage structure: { [user_id]: { [session_id]: Conversation } }
// This allows us to track all session_ids per user_id
export function getConversations(userId?: string | null): any[] {
  if (typeof window === 'undefined') return []
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return []
    const parsed = JSON.parse(data)
    
    // If no user_id provided, try to get it from storage
    if (!userId) {
      userId = getUserId()
    }
    
    // If still no user_id, return all conversations (for backward compatibility)
    if (!userId) {
      // Flatten all user conversations into a single array
      const allConversations: any[] = []
      Object.values(parsed).forEach((userConvs: any) => {
        if (typeof userConvs === 'object' && userConvs !== null) {
          Object.values(userConvs).forEach((conv: any) => {
            if (conv && typeof conv === 'object' && conv.session_id) {
              allConversations.push(conv)
            }
          })
        }
      })
      return allConversations
    }
    
    // Return conversations for specific user_id
    const userConversations = parsed[userId]
    if (!userConversations || typeof userConversations !== 'object') {
      return []
    }
    
    // Convert object of session_ids to array, ensuring all have session_id
    const conversations = Object.values(userConversations).filter(
      (conv: any) => conv && typeof conv === 'object' && conv.session_id
    ) as any[]
    
    console.log(`Retrieved ${conversations.length} conversations for user ${userId}`)
    return conversations
  } catch (error) {
    console.error('Error getting conversations:', error)
    return []
  }
}

export function saveConversations(conversations: any[], userId?: string | null): void {
  if (typeof window === 'undefined') return
  if (!userId) {
    // If no user_id, save as flat structure (backward compatibility)
    try {
      localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify({ conversations }))
    } catch (error) {
      console.error('Failed to save conversations:', error)
    }
    return
  }
  
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    const parsed = data ? JSON.parse(data) : {}
    
    // Convert array to object keyed by session_id
    const userConversations: { [sessionId: string]: any } = {}
    conversations.forEach((conv) => {
      if (conv && conv.session_id) {
        userConversations[conv.session_id] = conv
      }
    })
    
    // Store under user_id
    parsed[userId] = userConversations
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
  } catch (error) {
    console.error('Failed to save conversations:', error)
  }
}

export function addConversation(conversation: any, userId?: string | null): void {
  if (!userId) {
    userId = getUserId()
  }
  
  if (!userId) {
    console.warn('Cannot add conversation: no user_id available')
    return
  }
  
  if (!conversation || !conversation.session_id) {
    console.warn('Cannot add conversation: missing session_id')
    return
  }
  
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    const parsed = data ? JSON.parse(data) : {}
    
    // Get or create user's conversations object
    if (!parsed[userId] || typeof parsed[userId] !== 'object') {
      parsed[userId] = {}
    }
    
    // Ensure conversation has user_id set (for consistency)
    const conversationWithUserId = { ...conversation }
    
    // Add or update conversation by session_id
    const existingConv = parsed[userId][conversation.session_id]
    if (existingConv) {
      // Update existing conversation (merge to preserve other fields)
      parsed[userId][conversation.session_id] = { ...existingConv, ...conversationWithUserId }
    } else {
      // Add new conversation
      parsed[userId][conversation.session_id] = conversationWithUserId
    }
    
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
    console.log(`Conversation ${conversation.session_id} saved for user ${userId}. Total conversations: ${Object.keys(parsed[userId]).length}`)
  } catch (error) {
    console.error('Failed to add conversation:', error)
  }
}

export function updateConversation(
  sessionId: string,
  updates: Partial<any>,
  userId?: string | null
): void {
  if (!userId) {
    userId = getUserId()
  }
  
  if (!userId) {
    console.warn('Cannot update conversation: no user_id available')
    return
  }
  
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return
    
    const parsed = JSON.parse(data)
    const userConversations = parsed[userId]
    
    if (userConversations && userConversations[sessionId]) {
      userConversations[sessionId] = { ...userConversations[sessionId], ...updates }
      parsed[userId] = userConversations
      localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
    }
  } catch (error) {
    console.error('Failed to update conversation:', error)
  }
}

export function removeConversation(sessionId: string, userId?: string | null): void {
  if (!userId) {
    userId = getUserId()
  }
  
  if (!userId) {
    console.warn('Cannot remove conversation: no user_id available')
    return
  }
  
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return
    
    const parsed = JSON.parse(data)
    const userConversations = parsed[userId]
    
    if (userConversations && userConversations[sessionId]) {
      delete userConversations[sessionId]
      parsed[userId] = userConversations
      localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
    }
  } catch (error) {
    console.error('Failed to remove conversation:', error)
  }
}

// Get all session_ids for a specific user_id
export function getUserSessionIds(userId?: string | null): string[] {
  if (!userId) {
    userId = getUserId()
  }
  
  if (!userId) {
    return []
  }
  
  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return []
    
    const parsed = JSON.parse(data)
    const userConversations = parsed[userId]
    
    if (!userConversations || typeof userConversations !== 'object') {
      return []
    }
    
    // Return all session_ids (keys) for this user
    return Object.keys(userConversations)
  } catch {
    return []
  }
}

export function getMessages(sessionId: string): any[] {
  if (typeof window === 'undefined') return []
  if (!sessionId) {
    console.warn('getMessages called without sessionId - returning empty array')
    return []
  }
  try {
    const data = localStorage.getItem(MESSAGES_KEY)
    if (!data) return []
    const parsed = JSON.parse(data)
    // CRITICAL: Return messages ONLY for the specified session_id
    // Create a deep copy to prevent reference issues
    const messages = parsed[sessionId]
    const result = messages ? JSON.parse(JSON.stringify(messages)) : []
    console.log(`Retrieved ${result.length} messages for session ${sessionId}`)
    return result
  } catch (error) {
    console.error('Error getting messages:', error)
    return []
  }
}

export function saveMessages(sessionId: string, messages: any[]): void {
  if (typeof window === 'undefined') return
  if (!sessionId) {
    console.warn('saveMessages called without sessionId - messages will not be saved')
    return
  }
  try {
    const data = localStorage.getItem(MESSAGES_KEY)
    const parsed = data ? JSON.parse(data) : {}
    // CRITICAL: Ensure messages are stored uniquely by session_id
    // Create a deep copy to prevent reference issues
    parsed[sessionId] = JSON.parse(JSON.stringify(messages))
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(parsed))
    console.log(`Saved ${messages.length} messages for session ${sessionId}`)
  } catch (error) {
    console.error('Failed to save messages:', error)
  }
}

export function deleteMessages(sessionId: string): void {
  if (typeof window === 'undefined') return
  try {
    const data = localStorage.getItem(MESSAGES_KEY)
    if (!data) return
    const parsed = JSON.parse(data)
    delete parsed[sessionId]
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(parsed))
  } catch (error) {
    console.error('Failed to delete messages:', error)
  }
}

export function clearAllStorage(): void {
  if (typeof window === 'undefined') return
  try {
    removeToken()
    removeUserId()
    localStorage.removeItem(CONVERSATIONS_KEY)
    localStorage.removeItem(MESSAGES_KEY)
    console.log('All local storage cleared')
  } catch (error) {
    console.error('Failed to clear all storage:', error)
  }
}

