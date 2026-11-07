import type { Message } from '../types/message'
import { messagesForPersistence } from '../utils/storage'

/** Apply only this view's changes, preserving messages added by another tab. */
export function mergeMessageHistory(
  previous: Message[], incoming: Message[], stored: Message[],
): Message[] {
  const before = new Map(previous.map(message => [message.id, message]))
  const after = new Map(incoming.map(message => [message.id, message]))
  const merged = new Map(stored.map(message => [message.id, message]))
  const comparable = (message: Message | undefined): Message | undefined => {
    if (!message) return undefined
    const copy = { ...message }
    delete copy.order
    return copy
  }
  const same = (left: Message | undefined, right: Message | undefined): boolean =>
    JSON.stringify(comparable(left)) === JSON.stringify(comparable(right))
  for (const [id, message] of before) {
    if (!after.has(id) && same(merged.get(id), message)) merged.delete(id)
  }
  for (const [id, message] of after) {
    if (!same(before.get(id), message)) merged.set(id, message)
  }

  return orderMessageHistory([...merged.values()])
}

/** Keep admitted turns together when cached and server history overlap. */
export function orderMessageHistory(messages: Message[]): Message[] {
  const groups = new Map<string, Message[]>()
  for (const message of messages) {
    const key = message.interactionId || message.id
    const group = groups.get(key) ?? []
    group.push(message)
    groups.set(key, group)
  }
  const time = (messages: Message[]): number => Math.min(...messages.map(message => {
    const value = Date.parse(message.timestamp)
    return Number.isFinite(value) ? value : 0
  }))
  return [...groups.values()]
    .sort((left, right) => time(left) - time(right))
    .flatMap(group => group.filter(message => message.role === 'user').concat(
      group.filter(message => message.role !== 'user'),
    ))
    .map((message, order) => ({ ...message, order }))
}

/** One writer per mounted chat view; Web Locks serialize cache updates across tabs. */
export function createMessageHistoryWriter(storage: {
  read: (sessionId: string) => Message[]
  write: (sessionId: string, messages: Message[]) => void
}): {
  remember: (sessionId: string, messages: Message[]) => void
  save: (sessionId: string, messages: Message[]) => void
} {
  const baselines = new Map<string, Message[]>()
  let pending = Promise.resolve()
  const snapshot = (messages: Message[]): Message[] => structuredClone(
    messagesForPersistence(messages.map(message => ({ ...message }))),
  )
  const remember = (sessionId: string, messages: Message[]): void => {
    // Retain only recent view baselines, never another transcript database.
    baselines.delete(sessionId)
    baselines.set(sessionId, snapshot(messages))
    if (baselines.size > 8) baselines.delete(baselines.keys().next().value!)
  }
  return {
    remember,
    save(sessionId, messages) {
      const previous = baselines.get(sessionId) ?? []
      const incoming = snapshot(messages)
      remember(sessionId, incoming)
      const commit = (): void => storage.write(sessionId, mergeMessageHistory(
        previous, incoming, storage.read(sessionId),
      ))
      if (typeof navigator !== 'undefined' && navigator.locks) {
        pending = pending.then(() => navigator.locks.request(
          `jvchat-history:${sessionId}`, commit,
        )).catch(error => console.error('Failed to merge conversation cache:', error))
      } else {
        // Same-view compatibility on browsers without Web Locks. Concurrent
        // cross-tab writes are only qualified where that public API exists.
        commit()
      }
    },
  }
}
