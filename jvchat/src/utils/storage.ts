const TOKEN_KEY = 'jvchat_token'
const REFRESH_TOKEN_KEY = 'jvchat_refresh_token'
const INTERACT_SESSION_TOKENS_KEY = 'jvchat_interact_session_tokens'
const USER_ID_KEY = 'jvchat_user_id'
const CONVERSATIONS_KEY = 'jvchat_conversations'
const MESSAGES_KEY = 'jvchat_messages'
const SELECTED_AGENT_KEY = 'jvchat_selected_agent'
const SAVED_CREDENTIALS_KEY = 'jvchat_saved_credentials_v2'
const DEBUG_INTERACTIONS_PAGE_SIZE_KEY = 'jvchat_debug_interactions_page_size'
const DEBUG_INTERACTIONS_USER_FILTER_KEY = 'jvchat_debug_interactions_user_filter'
const TOOL_JSON_EXPAND_DEPTH_KEY = 'jvchat_tool_json_expand_depth'

/**
 * Attempt to write to localStorage with automatic quota management.
 * If the write fails due to QuotaExceededError, this prunes oldest message
 * sessions (and then oldest conversation entries) one by one until the write
 * succeeds or there is nothing left to prune.
 */
function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch (quotaError: any) {
    const isQuotaError =
      quotaError instanceof DOMException &&
      (quotaError.name === 'QuotaExceededError' ||
        quotaError.code === DOMException.QUOTA_EXCEEDED_ERR ||
        (typeof quotaError.message === 'string' &&
          /quota/i.test(quotaError.message)))
    if (!isQuotaError) {
      throw quotaError
    }
    console.warn(
      '[jvchat] localStorage quota exceeded. Pruning old data to free space…',
    )

    // First pass: prune oldest message sessions
    const pruned = pruneOldestData()
    if (!pruned) {
      console.error(
        '[jvchat] Could not free enough localStorage space; write failed.',
        quotaError,
      )
      return
    }

    // Retry the write
    try {
      localStorage.setItem(key, value)
    } catch (retryError: any) {
      const isStillQuota =
        retryError instanceof DOMException &&
        (retryError.name === 'QuotaExceededError' ||
          retryError.code === DOMException.QUOTA_EXCEEDED_ERR)
      if (isStillQuota) {
        console.error(
          '[jvchat] localStorage still full after pruning. Write failed.',
          retryError,
        )
      } else {
        throw retryError
      }
    }
  }
}

/**
 * Prune oldest data from localStorage to free space.
 * Removes message sessions oldest-first, then conversation entries.
 * Returns true if any data was pruned, false if nothing to prune.
 */
function pruneOldestData(): boolean {
  let pruned = false

  // 1. Prune oldest message sessions (by parsed order)
  try {
    const raw = localStorage.getItem(MESSAGES_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      const sessionIds = Object.keys(parsed)
      // Sort sessions so the one with the oldest first message is removed first
      const scored = sessionIds
        .map((sid) => {
          const msgs = parsed[sid]
          if (!Array.isArray(msgs) || msgs.length === 0) {
            return { sid, score: 0, empty: true }
          }
          const firstTs =
            msgs.find((m: any) => m.timestamp)?.timestamp ?? ''
          return {
            sid,
            score: firstTs ? new Date(firstTs).getTime() : 0,
            empty: false,
          }
        })
        .sort((a, b) => a.score - b.score)

      // Remove oldest sessions one at a time until we've freed enough space
      for (const entry of scored) {
        delete parsed[entry.sid]
        pruned = true
        try {
          localStorage.setItem(MESSAGES_KEY, JSON.stringify(parsed))
          return true
        } catch {
          // Still not enough — keep pruning
          continue
        }
      }

      // If all messages are pruned, clear the key entirely
      if (pruned) {
        try {
          localStorage.removeItem(MESSAGES_KEY)
        } catch {
          // best effort
        }
      }
    }
  } catch {
    // Parsing failed; nuke messages key entirely
    try {
      localStorage.removeItem(MESSAGES_KEY)
      pruned = true
    } catch {
      // best effort
    }
  }

  // 2. Prune oldest conversation entries for current user
  try {
    const raw = localStorage.getItem(CONVERSATIONS_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      const uid = getEffectiveUserId()
      const userConvs = uid ? parsed[uid] : null
      if (userConvs && typeof userConvs === 'object') {
        const entries = Object.entries(userConvs) as [string, any][]
        entries.sort(([, a], [, b]) => {
          const at = a?.last_message_at || a?.created_at || ''
          const bt = b?.last_message_at || b?.created_at || ''
          return new Date(at).getTime() - new Date(bt).getTime()
        })
        // Remove oldest conversations one at a time
        for (const [sid] of entries) {
          delete userConvs[sid]
          pruned = true
          try {
            localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
            return true
          } catch {
            continue
          }
        }
      }
    }
  } catch {
    // best effort
  }

  if (pruned) {
    try {
      localStorage.removeItem(CONVERSATIONS_KEY)
    } catch {
      // best effort
    }
  }

  return pruned
}

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

/**
 * Mode B interact session capability tokens (ADR-0020/0032), keyed by
 * session_id. The server mints one per web conversation; it must be resent as
 * `X-Session-Token` to resume, and refreshed via
 * `POST /agents/{id}/interact/session/refresh` when it nears expiry.
 */
const MAX_INTERACT_SESSION_TOKENS = 100

interface StoredInteractSessionToken {
  token: string
  savedAt: number
}

function readInteractSessionTokens(): Record<string, StoredInteractSessionToken> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = localStorage.getItem(INTERACT_SESSION_TOKENS_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function getInteractSessionToken(sessionId: string | undefined | null): string | null {
  if (!sessionId) return null
  const entry = readInteractSessionTokens()[sessionId]
  return entry?.token || null
}

export function setInteractSessionToken(sessionId: string, token: string): void {
  if (typeof window === 'undefined' || !sessionId || !token) return
  try {
    const map = readInteractSessionTokens()
    map[sessionId] = { token, savedAt: Date.now() }
    // Cap the map so abandoned sessions don't accumulate forever.
    const ids = Object.keys(map)
    if (ids.length > MAX_INTERACT_SESSION_TOKENS) {
      ids
        .sort((a, b) => (map[a]?.savedAt || 0) - (map[b]?.savedAt || 0))
        .slice(0, ids.length - MAX_INTERACT_SESSION_TOKENS)
        .forEach((id) => delete map[id])
    }
    safeSetItem(INTERACT_SESSION_TOKENS_KEY, JSON.stringify(map))
  } catch (error) {
    console.error('Failed to save interact session token:', error)
  }
}

export function removeInteractSessionToken(sessionId: string | undefined | null): void {
  if (typeof window === 'undefined' || !sessionId) return
  try {
    const map = readInteractSessionTokens()
    if (map[sessionId]) {
      delete map[sessionId]
      safeSetItem(INTERACT_SESSION_TOKENS_KEY, JSON.stringify(map))
    }
  } catch (error) {
    console.error('Failed to remove interact session token:', error)
  }
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

/** Decode `sub` / common id claims from the jvchat access token (JWT). */
function parseAccessTokenUserId(token: string | null): string | null {
  if (!token) return null
  try {
    const base64Url = token.split('.')[1]
    if (!base64Url) return null
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/')
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join(''),
    )
    const payload = JSON.parse(jsonPayload) as Record<string, unknown>
    const raw = payload.sub ?? payload.user_id ?? payload.userId ?? payload.id
    if (raw == null || raw === '') return null
    return String(raw)
  } catch {
    return null
  }
}

/** Read user id from JWT without persisting (used before sync). */
export function getUserIdFromAccessToken(): string | null {
  return parseAccessTokenUserId(getToken())
}

/**
 * Persist `jvchat_user_id` from the access token when login omitted `user.id`.
 * Ensures conversation/message keys match the account used for API calls.
 */
export function syncUserIdFromAccessToken(): string | null {
  const existing = getUserId()
  if (existing) return existing
  const fromJwt = getUserIdFromAccessToken()
  if (fromJwt) {
    setUserId(fromJwt)
    console.log('[jvchat] Restored user_id from access token for local persistence')
    return fromJwt
  }
  return null
}

export function getEffectiveUserId(): string | null {
  return getUserId() ?? syncUserIdFromAccessToken()
}

/** Migrate `{ conversations: [...] }` into `{ [userId]: { [sessionId]: conv } }`. */
function migrateLegacyConversationsRaw(parsed: Record<string, unknown>): Record<string, unknown> {
  const legacy = parsed.conversations
  if (!Array.isArray(legacy) || legacy.length === 0) return parsed

  const uid = getEffectiveUserId()
  if (!uid) return parsed

  let bucket = parsed[uid]
  if (!bucket || typeof bucket !== 'object' || Array.isArray(bucket)) {
    bucket = {}
    parsed[uid] = bucket
  }
  const b = bucket as Record<string, unknown>
  for (const conv of legacy) {
    if (conv && typeof conv === 'object' && conv !== null && 'session_id' in conv) {
      const sid = String((conv as { session_id: string }).session_id)
      if (sid) b[sid] = conv
    }
  }
  delete parsed.conversations
  try {
    safeSetItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
  } catch (e) {
    console.warn('[jvchat] Failed to persist migrated conversations', e)
  }
  return parsed
}

// Storage structure: { [user_id]: { [session_id]: Conversation } }
// This allows us to track all session_ids per user_id
export function getConversations(userId?: string | null): any[] {
  if (typeof window === 'undefined') return []
  try {
    syncUserIdFromAccessToken()
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return []
    let parsed = JSON.parse(data) as Record<string, unknown>
    parsed = migrateLegacyConversationsRaw(parsed)

    // If no user_id provided, try to get it from storage or JWT
    if (!userId) {
      userId = getEffectiveUserId()
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
    try {
      safeSetItem(CONVERSATIONS_KEY, JSON.stringify({ conversations }))
    } catch (error) {
      console.error('Failed to save conversations:', error)
    }
    return
  }

  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    const parsed = data ? JSON.parse(data) : {}

    const userConversations: { [sessionId: string]: any } = {}
    conversations.forEach((conv) => {
      if (conv && conv.session_id) {
        userConversations[conv.session_id] = conv
      }
    })

    parsed[userId] = userConversations
    safeSetItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
  } catch (error) {
    console.error('Failed to save conversations:', error)
  }
}

export function addConversation(conversation: any, userId?: string | null): void {
  if (!userId) {
    userId = getEffectiveUserId()
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

    if (!parsed[userId] || typeof parsed[userId] !== 'object') {
      parsed[userId] = {}
    }

    const conversationWithUserId = { ...conversation }

    const existingConv = parsed[userId][conversation.session_id]
    if (existingConv) {
      parsed[userId][conversation.session_id] = { ...existingConv, ...conversationWithUserId }
    } else {
      parsed[userId][conversation.session_id] = conversationWithUserId
    }

    safeSetItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
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
    userId = getEffectiveUserId()
  }

  if (!userId) {
    console.warn('Cannot update conversation: no user_id available')
    return
  }

  try {
    const data = localStorage.getItem(CONVERSATIONS_KEY)
    if (!data) return

    const parsed = JSON.parse(data) as Record<string, unknown>
    const userConversations = parsed[userId] as Record<string, unknown> | undefined

    if (userConversations && userConversations[sessionId]) {
      userConversations[sessionId] = {
        ...(userConversations[sessionId] as Record<string, unknown>),
        ...updates,
      }
      parsed[userId] = userConversations
      safeSetItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
    }
  } catch (error) {
    console.error('Failed to update conversation:', error)
  }
}

export function removeConversation(sessionId: string, userId?: string | null): void {
  if (!userId) {
    userId = getEffectiveUserId()
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
      safeSetItem(CONVERSATIONS_KEY, JSON.stringify(parsed))
    }
  } catch (error) {
    console.error('Failed to remove conversation:', error)
  }
}

// Get all session_ids for a specific user_id
export function getUserSessionIds(userId?: string | null): string[] {
  if (!userId) {
    userId = getEffectiveUserId()
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

/** Strip SSE debug payloads before localStorage (kept in React state for debug UI). */
export function messagesForPersistence<T extends Record<string, unknown>>(
  messages: T[],
): T[] {
  return messages.map((msg) => {
    if (!msg || typeof msg !== 'object' || !('debugData' in msg)) return msg
    const copy = { ...msg }
    delete copy.debugData
    return copy as T
  })
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
    if (!Array.isArray(result)) return []
    return result
      .filter((msg: any) => {
        if (!msg || typeof msg !== 'object') return false
        if (msg.category === 'thought' && !msg.interactionId) return false
        return true
      })
      .map((msg: any, idx: number) => ({
        ...msg,
        order:
          typeof msg.order === 'number' && Number.isFinite(msg.order)
            ? msg.order
            : idx,
      }))
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
    parsed[sessionId] = JSON.parse(JSON.stringify(messagesForPersistence(messages)))
    safeSetItem(MESSAGES_KEY, JSON.stringify(parsed))
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
    safeSetItem(MESSAGES_KEY, JSON.stringify(parsed))
  } catch (error) {
    console.error('Failed to delete messages:', error)
  }
}

/** Tokens and user identity only — keeps persisted conversations/messages/local preferences. */
export function clearAuthSession(): void {
  if (typeof window === 'undefined') return
  try {
    removeToken()
    removeRefreshToken()
    removeUserId()
  } catch (error) {
    console.error('Failed to clear auth session:', error)
  }
}

/** Nuclear option: auth + conversations + cached messages for all users in this browser. */
export function clearAllStorage(): void {
  if (typeof window === 'undefined') return
  try {
    clearAuthSession()
    localStorage.removeItem(CONVERSATIONS_KEY)
    localStorage.removeItem(MESSAGES_KEY)
    localStorage.removeItem(INTERACT_SESSION_TOKENS_KEY)
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

const DEFAULT_TOOL_JSON_EXPAND_DEPTH = 2

export function getToolJsonExpandDepth(): number {
  if (typeof window === 'undefined') return DEFAULT_TOOL_JSON_EXPAND_DEPTH
  try {
    const raw = localStorage.getItem(TOOL_JSON_EXPAND_DEPTH_KEY)
    if (raw == null) return DEFAULT_TOOL_JSON_EXPAND_DEPTH
    const n = parseInt(raw, 10)
    if (!Number.isFinite(n) || n < 0) return DEFAULT_TOOL_JSON_EXPAND_DEPTH
    return n
  } catch {
    return DEFAULT_TOOL_JSON_EXPAND_DEPTH
  }
}

export function setToolJsonExpandDepth(depth: number): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(TOOL_JSON_EXPAND_DEPTH_KEY, String(depth))
  } catch (error) {
    console.error('Failed to save tool JSON expand depth:', error)
  }
}

