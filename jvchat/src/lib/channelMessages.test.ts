import { describe, expect, it } from 'vitest'
import { mergeChannelMessage } from './channelMessages'
import type { ResponseMessageData } from '../types/api'

const frame = (patch: Partial<ResponseMessageData> = {}): ResponseMessageData => ({
  id: 'response', session_id: 'session', interaction_id: 'run', content: 'Hello', message_type: 'adhoc', channel: 'default', metadata: {}, ...patch,
})

describe('session channel projection', () => {
  const replay = (patch: Partial<ResponseMessageData> = {}): ResponseMessageData => frame({
    category: 'user', timestamp: '2026-11-05T01:00:01Z',
    metadata: { native_replay: true, historical_import: true, replay_input: { content: 'Earlier question', timestamp: '2026-11-05T01:00:00Z' } }, ...patch,
  })

  it('restores both roles once and places older history before newer cached turns', () => {
    let rows = mergeChannelMessage([], frame({ id: 'newer', interaction_id: 'newer-turn', timestamp: '2026-11-06T00:00:00Z' }), () => 0)
    rows = mergeChannelMessage(rows, replay(), () => 1)
    expect(rows.map(m => [m.role, m.content])).toEqual([['user', 'Earlier question'], ['assistant', 'Hello'], ['assistant', 'Hello']])
    expect(mergeChannelMessage(rows, replay(), () => 2)).toEqual(rows)
    expect(rows[1].observability).toBeUndefined()
  })

  it('preserves cached input and rebinds an unambiguous old final id', () => {
    const seed = [{ id: 'cached-user', role: 'user' as const, content: 'Local input', interactionId: 'run', timestamp: '2026-11-05T01:00:00Z' },
      { id: 'cached-answer', role: 'assistant' as const, content: 'Hello', interactionId: 'run', timestamp: '2026-11-05T01:00:01Z' }]
    const rows = mergeChannelMessage(seed, replay(), () => 0)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({ id: 'cached-user', content: 'Local input' })
    expect(rows[1].id).toBe('response')
  })

  it('does not overwrite an existing input id or collapse multi-message turns', () => {
    const user = { id: 'response', role: 'user' as const, content: 'Keep', timestamp: '2026-11-05T01:00:00Z' }
    expect(mergeChannelMessage([user], replay(), () => 0)).toEqual([user])
    const rows = ['one', 'two'].map(id => ({ id, role: 'assistant' as const, interactionId: 'run', content: id, timestamp: '2026-11-05T01:00:01Z' }))
    const merged = mergeChannelMessage(rows, replay(), () => 0)
    expect(merged.filter(m => m.role === 'assistant')).toHaveLength(3)
  })

  it.each([undefined, { content: 'Input', timestamp: 'invalid' }, { content: false, timestamp: '2026-11-05T01:00:00Z' }])('ignores malformed replay input %s', input => {
    const rows = mergeChannelMessage([], replay({ metadata: { native_replay: true, replay_input: input } }), () => 0)
    expect(rows).toHaveLength(1)
    expect(rows[0].role).toBe('assistant')
  })

  it('does not turn live metadata into an invented user message', () => {
    const rows = mergeChannelMessage([], replay({ metadata: { replay_input: { content: 'Input', timestamp: '2026-11-05T01:00:00Z' } } }), () => 0)
    expect(rows).toHaveLength(1)
  })

  it('does not duplicate complete backlog/live text or append replayed chunks', () => {
    let rows = mergeChannelMessage([], frame(), () => 0)
    rows = mergeChannelMessage(rows, frame(), () => 1)
    rows = mergeChannelMessage(rows, frame({ message_type: 'stream_chunk', metadata: { sequence: 0 } }), () => 2)
    rows = mergeChannelMessage(rows, frame({ message_type: 'final', content: '' }), () => 3)
    expect(rows).toHaveLength(1)
    expect(rows[0].content).toBe('Hello')
  })
  it('deduplicates chunk sequences and accepts an authoritative flush', () => {
    let rows = mergeChannelMessage([], frame({ message_type: 'stream_chunk', content: 'A', metadata: { sequence: 0 } }), () => 0)
    rows = mergeChannelMessage(rows, frame({ message_type: 'stream_chunk', content: 'A', metadata: { sequence: 0 } }), () => 1)
    rows = mergeChannelMessage(rows, frame({ message_type: 'stream_chunk', content: 'B', metadata: { sequence: 1 } }), () => 2)
    expect(rows[0].content).toBe('AB')
    rows = mergeChannelMessage(rows, frame({ content: 'ABC' }), () => 3)
    expect(rows[0].content).toBe('ABC')
    expect(rows[0].streaming).toBe(false)
  })
  it('keeps tool identity and bounded usage, and refuses an id from another interaction', () => {
    const rows = mergeChannelMessage([], frame({ category: 'thought', thought_type: 'tool_call', segment_id: 'call', metadata: { tool_name: 'read', observability: { usage: { total_tokens: 12, private_data: 'excluded' } } } }), () => 0)
    expect(rows[0].thoughtType).toBe('tool_call')
    expect(rows[0].observability?.usage).toEqual({ total_tokens: 12 })
    expect(mergeChannelMessage(rows, frame({ interaction_id: 'foreign' }), () => 1)).toBe(rows)
  })
})
