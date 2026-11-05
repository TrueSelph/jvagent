import type { Message } from '../types/message'
import type { ResponseMessageData } from '../types/api'
import { messageObservability } from './messageObservability'

/** ResponseBus ids and chunk sequence numbers make backlog/live overlap idempotent. */
export function mergeChannelMessage(messages: Message[], incoming: ResponseMessageData, nextOrder: () => number): Message[] {
  if (!incoming.id || !['adhoc', 'stream_chunk', 'final'].includes(incoming.message_type)) return messages
  const index = messages.findIndex(m => m.id === incoming.id)
  const previous = messages[index]
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
  return previous ? messages.map((m, i) => i === index ? updated : m) : [...messages, updated]
}
