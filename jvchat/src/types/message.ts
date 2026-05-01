/** Local outgoing attachments: blob previews are session-only; data URLs persist in localStorage. */
export interface UserMessageAttachmentPreview {
  name: string
  kind: 'image' | 'document'
  /** Ephemeral `blob:` URL (current session); not persisted across reload when persistedDataUrl is absent. */
  previewUrl?: string
  /** `data:image/...;base64,...` — stored with saveMessages and survives reload for that session. */
  persistedDataUrl?: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  streaming?: boolean
  debugData?: any // Store final message JSON for debug view
  interactionId?: string // Used to prevent cross-interaction overwrites
  /** Echo of server ResponseMessage.metadata (e.g. media_url, media_type) */
  metadata?: Record<string, unknown>
  /** Outgoing files shown inline on the user bubble (jvchat). */
  attachments?: UserMessageAttachmentPreview[]
  /** Logical response stream category. */
  category?: 'user' | 'thought'
  /** Thought subtype when category === "thought". */
  thoughtType?: 'reasoning' | 'tool_call' | 'tool_result' | 'status'
  /** Segment key grouping thought stream chunks. */
  segmentId?: string
  /** Loop iteration index when emitted by server. */
  iteration?: number
  /** Stable insertion order for deterministic grouping/rendering. */
  order?: number
  /** When this user message continues an edited branch, matches the root user message id. */
  branchRootId?: string
}

export interface StreamingMessage {
  id: string
  content: string
  complete: boolean
}

