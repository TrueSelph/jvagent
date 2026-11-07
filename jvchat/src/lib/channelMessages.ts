import type { Message } from '../types/message'
import type { ResponseMessageData } from '../types/api'
import { messageObservability } from './messageObservability'
import { orderMessageHistory } from './messageHistory'

/** ResponseBus ids and chunk sequence numbers make backlog/live overlap idempotent. */
export function mergeChannelMessage(messages: Message[], incoming: ResponseMessageData, nextOrder: () => number): Message[] {
  if (!incoming.id || !['adhoc', 'stream_chunk', 'final'].includes(incoming.message_type)) return messages
  const replay = incoming.metadata?.native_replay === true && incoming.category === 'user' && incoming.message_type === 'adhoc'
  let index = messages.findIndex(m => m.id === incoming.id)
  if (index < 0 && replay && incoming.interaction_id) {
    const candidates = messages.filter(m => m.role === 'assistant' && m.category !== 'thought' && m.interactionId === incoming.interaction_id)
    // Historical clients used different message ids. Rebind only an unambiguous
    // final answer; multi-message Action turns retain their separate identities.
    if (candidates.length === 1) index = messages.indexOf(candidates[0])
  }
  const previous = messages[index]
  if (previous?.role === 'user') return messages
  if (previous?.interactionId && incoming.interaction_id && previous.interactionId !== incoming.interaction_id) return messages
  if (incoming.message_type === 'final') return previous?.streaming
    ? messages.map((m, i) => i === index ? { ...m, streaming: false } : m) : messages
  const chunk = incoming.message_type === 'stream_chunk'
  if (chunk && previous) {
    const sequence = incoming.metadata?.sequence
    if (!previous.streaming || typeof sequence !== 'number' || sequence <= Number(previous.metadata?.sequence ?? -1)) return messages
  }
  if (!incoming.content && !previous) return messages
  const thought = incoming.category === 'thought' || (incoming.category !== 'user' && Boolean(incoming.thought_type))
  const updated: Message = {
    ...previous,
    id: incoming.id, role: 'assistant', interactionId: incoming.interaction_id || previous?.interactionId,
    content: chunk ? (previous?.content ?? '') + incoming.content : incoming.content,
    timestamp: previous?.timestamp ?? incoming.timestamp ?? new Date().toISOString(),
    streaming: chunk, category: thought ? 'thought' : 'user', thoughtType: thought ? incoming.thought_type : undefined,
    segmentId: incoming.segment_id, metadata: { ...previous?.metadata, ...incoming.metadata },
    observability: messageObservability({ observability: incoming.metadata?.observability }) ?? previous?.observability,
    order: previous?.order ?? nextOrder(),
  }
  let result = previous ? messages.map((m, i) => i === index ? updated : m) : [...messages, updated]
  const input = incoming.metadata?.replay_input
  if (replay && incoming.interaction_id && input && typeof input.content === 'string' && input.content && typeof input.timestamp === 'string' && Number.isFinite(Date.parse(input.timestamp)) && !messages.some(m => m.role === 'user' && m.interactionId === incoming.interaction_id)) {
    result = [...result, {
      id: `replay-user:${incoming.interaction_id}`, role: 'user', interactionId: incoming.interaction_id,
      content: input.content, timestamp: input.timestamp, order: nextOrder(),
    }]
  }
  return replay ? orderMessageHistory(result) : result
}
