import { describe, expect, it, vi } from 'vitest'
import { createMessageHistoryWriter, mergeMessageHistory } from './messageHistory'
import { getMessages, saveMessages } from '../utils/storage'
import type { Message } from '../types/message'

const message = (id: string, content = id, interactionId = id, time = 0): Message => ({
  id, content, interactionId, role: 'user', timestamp: new Date(time).toISOString(),
})

describe('conversation cache convergence', () => {
  it('preserves disjoint turns from two stale chat views', () => {
    localStorage.clear()
    const storage = { read: getMessages, write: saveMessages }
    const first = createMessageHistoryWriter(storage)
    const second = createMessageHistoryWriter(storage)
    const seed = message('seed')
    saveMessages('session', [seed])
    first.remember('session', [seed])
    second.remember('session', [seed])
    first.save('session', [seed, message('first', 'lookup', 'one', 1)])
    second.save('session', [seed, message('second', 'recall', 'two', 2)])
    first.save('session', [seed, message('first', 'lookup', 'one', 1), {
      ...message('reply', 'result', 'one', 3), role: 'assistant',
    }])
    expect(getMessages('session').map(value => value.id)).toEqual([
      'seed', 'first', 'reply', 'second',
    ])
  })

  it('does not resurrect an edited-away turn from an unchanged stale view', () => {
    const original = [message('seed'), message('old')]
    const edited = mergeMessageHistory(original, [original[0], message('replacement')], original)
    const stale = mergeMessageHistory(original, [...original, message('later')], edited)
    expect(stale.map(value => value.id)).toEqual(['seed', 'replacement', 'later'])
  })

  it('preserves foreign edits when another view updates only its own turn', () => {
    const base = [message('seed'), message('one')]
    const incoming = [...base, message('two')]
    const stored = [message('seed', 'edited remotely'), message('one')]
    expect(mergeMessageHistory(base, incoming, stored)[0].content).toBe('edited remotely')
  })

  it('ignores presentation order changes when deleting a branch', () => {
    const before = [{ ...message('old'), order: 10 }]
    expect(mergeMessageHistory(before, [], [{ ...before[0], order: 0 }])).toEqual([])
  })

  it('serializes tab writes through Web Locks and snapshots the call-time content', async () => {
    localStorage.clear()
    const requests: Array<() => void> = []
    const request = vi.fn((_name: string, callback: () => void) => new Promise<void>(resolve => {
      requests.push(() => { callback(); resolve() })
    }))
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } })
    try {
      const writer = createMessageHistoryWriter({ read: getMessages, write: saveMessages })
      const original = [message('one')]
      writer.save('session', original)
      original[0].content = 'mutated afterwards'
      await vi.waitFor(() => expect(requests).toHaveLength(1))
      expect(getMessages('session')).toEqual([])
      requests[0]()
      await vi.waitFor(() => expect(getMessages('session')[0]?.content).toBe('one'))
      expect(request).toHaveBeenCalledWith('jvchat-history:session', expect.any(Function))
    } finally {
      Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    }
  })

  it('keeps private debug payloads out of a merged cache', () => {
    localStorage.clear()
    const writer = createMessageHistoryWriter({ read: getMessages, write: saveMessages })
    writer.save('session', [{ ...message('one'), debugData: { private: 'private-payload' } }])
    expect(localStorage.getItem('jvchat_messages')).not.toContain('private-payload')
  })
})
