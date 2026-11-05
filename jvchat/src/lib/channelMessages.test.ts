import { describe, expect, it } from 'vitest'
import { mergeChannelMessage } from './channelMessages'
import type { ResponseMessageData } from '../types/api'

const frame = (patch: Partial<ResponseMessageData> = {}): ResponseMessageData => ({
  id: 'response', session_id: 'session', interaction_id: 'run', content: 'Hello', message_type: 'adhoc', channel: 'default', metadata: {}, ...patch,
})

describe('session channel projection', () => {
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
