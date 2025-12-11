export interface SSEEvent {
  type: string
  data: any
}

export function parseSSEChunk(chunk: string): SSEEvent | null {
  const lines = chunk.split('\n')
  let eventType = 'message'
  let data: any = null

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventType = line.substring(6).trim()
    } else if (line.startsWith('data:')) {
      const dataStr = line.substring(5).trim()
      try {
        data = JSON.parse(dataStr)
      } catch {
        data = dataStr
      }
    }
  }

  if (data === null) return null

  return { type: eventType, data }
}

export async function* streamSSE(
  response: Response
): AsyncGenerator<SSEEvent, void, unknown> {
  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('Response body is not readable')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''

      for (const chunk of chunks) {
        if (chunk.trim()) {
          const event = parseSSEChunk(chunk)
          if (event) {
            yield event
          }
        }
      }
    }

    if (buffer.trim()) {
      const event = parseSSEChunk(buffer)
      if (event) {
        yield event
      }
    }
  } finally {
    reader.releaseLock()
  }
}

