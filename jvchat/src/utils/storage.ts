const TOKEN_KEY = 'jvchat_token'
const REFRESH_TOKEN_KEY = 'jvchat_refresh_token'
const USER_ID_KEY = 'jvchat_user_id'
const CONVERSATIONS_KEY = 'jvchat_conversations'
const MESSAGES_KEY = 'jvchat_messages'
const SELECTED_AGENT_KEY = 'jvchat_selected_agent'
const SAVED_CREDENTIALS_KEY = 'jvchat_saved_credentials_v2'
const DEBUG_INTERACTIONS_PAGE_SIZE_KEY = 'jvchat_debug_interactions_page_size'
const DEBUG_INTERACTIONS_USER_FILTER_KEY = 'jvchat_debug_interactions_user_filter'

export const DEBUG_INTERACTIONS_PAGE_SIZES = [10, 20, 40, 80, 100] as const
export type DebugInteractionsPageSize =
  (typeof DEBUG_INTERACTIONS_PAGE_SIZES)[number]

const DEFAULT_DEBUG_INTERACTIONS_PAGE_SIZE: DebugInteractionsPageSize = 10

function isAllowedPageSize(n: number): n is DebugInteractionsPageSize {
  return (DEBUG_INTERACTIONS_PAGE_SIZES as readonly number[]).includes(n)
}

export interface SavedCredential {
  id: string
  serverUrl: string
  email: string
  password: string
  name?: string
  createdAt: number
}

export function getSavedCredentials(): SavedCredential[] {
  if (typeof window === 'undefined') return []
  try {
    const data = localStorage.getItem(SAVED_CREDENTIALS_KEY)
    if (!data) return []
    const parsed = JSON.parse(data)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function addSavedCredential(cred: Omit<SavedCredential, 'id' | 'createdAt'>): SavedCredential {
  if (typeof window === 'undefined') throw new Error('Cannot add credential')
  const list = getSavedCredentials()
  const id = `cred_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
  const created = Date.now()
  const newCred: SavedCredential = {
    ...cred,
    id,
    createdAt: created,
  }
  const updated = [...list, newCred]
  localStorage.setItem(SAVED_CREDENTIALS_KEY, JSON.stringify(updated))
  return newCred
}

export function updateSavedCredential(id: string, updates: Partial<SavedCredential>): void {
  if (typeof window === 'undefined') return
  const list = getSavedCredentials()
  const idx = list.findIndex((c) => c.id === id)
  if (idx === -1) return
  list[idx] = { ...list[idx], ...updates }
  localStorage.setItem(SAVED_CREDENTIALS_KEY, JSON.stringify(list))
}

export function removeSavedCredential(id: string): void {
  if (typeof window === 'undefined') return
  const list = getSavedCredentials().filter((c) => c.id !== id)
  localStorage.setItem(SAVED_CREDENTIALS_KEY, JSON.stringify(list))
}

export function upsertSavedCredential(cred: Omit<SavedCredential, 'id' | 'createdAt'>): SavedCredential {
  const list = getSavedCredentials()
  const existing = list.find(
    (c) => c.serverUrl === cred.serverUrl && c.email === cred.email
  )
  if (existing) {
    updateSavedCredential(existing.id, {
      ...cred,
      password: cred.password,
      name: cred.name,
    })
    return { ...existing, ...cred }
  }
  return addSavedCredential(cred)
}

/** Portable account row for JSON export/import (no id / createdAt). */
export type SavedCredentialPortable = Pick<
  SavedCredential,
  'serverUrl' | 'email' | 'password'
> & { name?: string }

const SAVED_ACCOUNTS_EXPORT_VERSION = 1 as const

export interface SavedAccountsExportFile {
  jvchatSavedAccounts: typeof SAVED_ACCOUNTS_EXPORT_VERSION
  exportedAt: string
  accounts: SavedCredentialPortable[]
}

function newSavedCredentialId(): string {
  return `cred_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`
}

export function buildSavedAccountsExportJson(): string {
  const accounts: SavedCredentialPortable[] = getSavedCredentials().map((c) => ({
    serverUrl: c.serverUrl,
    email: c.email,
    password: c.password,
    ...(c.name ? { name: c.name } : {}),
  }))
  const payload: SavedAccountsExportFile = {
    jvchatSavedAccounts: SAVED_ACCOUNTS_EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    accounts,
  }
  return JSON.stringify(payload, null, 2)
}

function normalizePortableEntry(
  raw: unknown,
): { ok: true; item: SavedCredentialPortable } | { ok: false } {
  if (!raw || typeof raw !== 'object') return { ok: false }
  const o = raw as Record<string, unknown>
  const serverUrl = typeof o.serverUrl === 'string' ? o.serverUrl.trim() : ''
  const email = typeof o.email === 'string' ? o.email.trim() : ''
  const password = typeof o.password === 'string' ? o.password : ''
  const nameRaw = typeof o.name === 'string' ? o.name.trim() : ''
  const name = nameRaw || undefined
  if (!serverUrl || !email || !password) return { ok: false }
  return { ok: true, item: { serverUrl, email, password, name } }
}

export function parseSavedAccountsImport(
  text: string,
):
  | { ok: true; accounts: SavedCredentialPortable[] }
  | { ok: false; error: string } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'Invalid JSON file.' }
  }

  let rows: unknown[] = []
  if (Array.isArray(parsed)) {
    rows = parsed
  } else if (parsed && typeof parsed === 'object') {
    const o = parsed as Record<string, unknown>
    if (Array.isArray(o.accounts)) rows = o.accounts
    else if (Array.isArray(o.savedAccounts)) rows = o.savedAccounts
  }

  const accounts: SavedCredentialPortable[] = []
  for (const row of rows) {
    const n = normalizePortableEntry(row)
    if (n.ok) accounts.push(n.item)
  }
  if (accounts.length === 0) {
    return { ok: false, error: 'No valid accounts found in file.' }
  }
  return { ok: true, accounts }
}

export function importSavedAccountsPortable(
  accounts: SavedCredentialPortable[],
  mode: 'merge' | 'replace',
): void {
  if (typeof window === 'undefined') return
  if (mode === 'replace') {
    const base = Date.now()
    const next: SavedCredential[] = accounts.map((a, i) => ({
      serverUrl: a.serverUrl,
      email: a.email,
      password: a.password,
      name: a.name,
      id: newSavedCredentialId(),
      createdAt: base + i,
    }))
    localStorage.setItem(SAVED_CREDENTIALS_KEY, JSON.stringify(next))
    return
  }
  for (const a of accounts) {
    upsertSavedCredential(a)
  }
}

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

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setRefreshToken(token: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(REFRESH_TOKEN_KEY, token)
}

export function removeRefreshToken(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(REFRESH_TOKEN_KEY)
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
    removeRefreshToken()
    removeUserId()
    localStorage.removeItem(CONVERSATIONS_KEY)
    localStorage.removeItem(MESSAGES_KEY)
  } catch (error) {
    console.error('Failed to clear all storage:', error)
  }
}

export function saveSelectedAgent(agentName: string): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(SELECTED_AGENT_KEY, agentName)
  } catch (error) {
    console.error('Failed to save selected agent:', error)
  }
}

export function getSelectedAgent(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(SELECTED_AGENT_KEY)
}

export function removeSelectedAgent(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(SELECTED_AGENT_KEY)
}

export function getDebugInteractionsPageSize(): DebugInteractionsPageSize {
  if (typeof window === 'undefined') return DEFAULT_DEBUG_INTERACTIONS_PAGE_SIZE
  try {
    const raw = localStorage.getItem(DEBUG_INTERACTIONS_PAGE_SIZE_KEY)
    if (raw == null) return DEFAULT_DEBUG_INTERACTIONS_PAGE_SIZE
    const n = parseInt(raw, 10)
    if (!Number.isFinite(n) || !isAllowedPageSize(n)) {
      return DEFAULT_DEBUG_INTERACTIONS_PAGE_SIZE
    }
    return n
  } catch {
    return DEFAULT_DEBUG_INTERACTIONS_PAGE_SIZE
  }
}

export function setDebugInteractionsPageSize(size: DebugInteractionsPageSize): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(DEBUG_INTERACTIONS_PAGE_SIZE_KEY, String(size))
  } catch (error) {
    console.error('Failed to save debug interactions page size:', error)
  }
}

export function getDebugInteractionsUserFilter(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(DEBUG_INTERACTIONS_USER_FILTER_KEY)
    if (raw == null || raw === '') return null
    return raw
  } catch {
    return null
  }
}

export function setDebugInteractionsUserFilter(userId: string | null): void {
  if (typeof window === 'undefined') return
  try {
    if (userId == null || userId === '') {
      localStorage.removeItem(DEBUG_INTERACTIONS_USER_FILTER_KEY)
    } else {
      localStorage.setItem(DEBUG_INTERACTIONS_USER_FILTER_KEY, userId)
    }
  } catch (error) {
    console.error('Failed to save debug interactions user filter:', error)
  }
}

export function isTokenExpired(token: string | null): boolean {
  if (!token) return true
  try {
    const base64Url = token.split('.')[1]
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/')
    const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
        return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)
    }).join(''))
    const payload = JSON.parse(jsonPayload)
    const now = Math.floor(Date.now() / 1000)
    // Return true if expired or expiring in the next 30 seconds
    return payload.exp < now + 30
  } catch (e) {
    return true
  }
}

export function cleanupOldStorage(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem('jvchat_saved_credentials')
  localStorage.removeItem('jvchat_auth_creds')
  // Keep jvchat_saved_credentials_v2 - that's the current format
}

