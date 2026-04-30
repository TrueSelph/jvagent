export interface LoginRequest {
  email: string
  password: string
  serverUrl?: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  refresh_token?: string
  refresh_expires_in?: number
  user: {
    id: string
    email: string
    name: string
    created_at: string
    is_active: boolean
  }
}

export interface TokenRefreshRequest {
  refresh_token: string
}

export type TokenRefreshResponse = LoginResponse

/** Docling PDF OCR backend for PageIndex / jvforge ingest (RapidOCR only). */
export type DoclingOcrEngine = 'none' | 'rapidocr'

export interface Agent {
  id: string
  namespace: string
  name: string
  alias?: string
  enabled: boolean
  description?: string
  interaction_limit?: number
}

export interface AgentsResponse {
  agents: Agent[]
  total: number
  page: number
  per_page: number
  total_pages: number
  has_previous: boolean
  has_next: boolean
  previous_page?: number
  next_page?: number
}

/** Single media item for `data.image_urls` or `data.whatsapp_media` (mirror of jvagent visitor.data). */
export type InteractMediaEntry =
  | string
  | {
      url?: string
      base64?: string
      mime_type?: string
      detail?: 'auto' | 'low' | 'high'
      filename?: string
    }

/** Payload for `POST .../interact` body field `data` (→ InteractWalker.data). */
export interface InteractGuestDataPayload {
  image_urls?: InteractMediaEntry[]
  whatsapp_media?: InteractMediaEntry[]
  image_interpretation?: boolean
}

export interface InteractionRequest {
  utterance: string
  channel?: string
  session_id?: string
  user_id?: string
  stream?: boolean
  data?: InteractGuestDataPayload
}

export interface InteractionResponse {
  user_id: string
  session_id: string
  response?: string
  interaction: {
    id: string
    utterance: string
    response?: string
    actions: string[]
    directives: string[]
    parameters: any[]
    model_log: any[]
    messages: string[]
    streamed: boolean
  }
  report: any[]
}

export interface ResponseMessageData {
  id: string
  session_id: string
  interaction_id: string
  message_type: string
  content: string
  channel: string
  category?: 'user' | 'thought'
  thought_type?: 'reasoning' | 'tool_call' | 'tool_result' | 'status'
  segment_id?: string
  metadata: Record<string, any>
  observability_data?: Record<string, any>
  // timestamp is omitted for stream_chunk messages (not useful - chunks arrive in order,
  // timestamp only needed once when creating message bubble, client can timestamp on receipt)
  timestamp?: string | null
  // delivered is omitted from stream payloads - only meaningful for channel adapters
  // tracking external API delivery, not for direct SSE streaming
}

export interface SSEChunk {
  type: 'start' | 'message' | 'final' | 'error'
  interaction_id?: string
  session_id?: string
  user_id?: string
  message?: ResponseMessageData | string // ResponseMessageData when type === 'message', string when type === 'error'
  interaction?: InteractionResponse['interaction']
  report?: any[]
}

export interface LogEntry {
  log_id: string
  log_level: string
  status_code: number
  event_code: string
  message: string
  path: string
  method: string
  agent_id?: string
  logged_at: string
  log_data: Record<string, unknown>
}

export interface LogsPagination {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface LogsResponse {
  logs: LogEntry[]
  pagination: LogsPagination
}

/** PageIndex document (agent-scoped collection = agent_id) */
export interface PageIndexDocument {
  doc_name: string
  doc_description?: string
  doc_url?: string
  root_id: string
  collection_name?: string
  metadata?: Record<string, unknown>
  /** Number of DocumentNode chunks (from API); omit on older backends */
  chunks?: number
}

export interface PageIndexListResponse {
  documents: PageIndexDocument[]
}

export interface PageIndexUploadResponse {
  doc_name: string
  root_id: string
  doc_description?: string
  /** Present after sync ingest; 0 for async until documents are listed again */
  chunks?: number
}

export interface PageIndexDeleteResponse {
  message: string
}

/** PATCH document root metadata response */
export interface PageIndexDocumentMetadataResponse {
  doc_name: string
  root_id: string
  metadata?: Record<string, unknown> | null
  doc_url?: string | null
}

/** Partial updates for PATCH …/pageindex/documents/{docName} */
export interface PageIndexDocumentPatchUpdates {
  metadata?: Record<string, unknown> | null
  doc_url?: string | null
}

export interface PageIndexSearchResult {
  node_id?: string
  title?: string
  doc_name?: string
  content?: string
  text?: string
  summary?: string
  start_index?: number
  end_index?: number
  doc_url?: string
}

export interface PageIndexSearchResponse {
  results: PageIndexSearchResult[]
}

export interface PageIndexSearchParams {
  query: string
  doc_name?: string
  strategy?: 'tree_search' | 'direct' | 'walker'
  limit?: number
  metadata?: Record<string, unknown>
  /** When false, search results omit doc_url */
  include_references?: boolean
}

/** PageIndex document chunk (DocumentNode) */
export interface PageIndexChunk {
  id: string
  title: string
  text: string
  summary?: string | null
  prefix_summary?: string | null
  structure: string
  node_id: string
  start_index?: number | null
  end_index?: number | null
  physical_index?: number | null
  line_num?: number | null
  doc_name: string
  /** When false, chunk is excluded from RAG if only_enabled (default true from API) */
  enabled?: boolean
  content_type?: string | null
  hierarchy?: string[] | null
}

export interface PageIndexChunksListResponse {
  chunks: PageIndexChunk[]
  total: number
}

export interface PageIndexChunkUpdatePayload {
  title?: string
  text?: string
  summary?: string | null
  prefix_summary?: string | null
  structure?: string
  node_id?: string
  start_index?: number | null
  end_index?: number | null
  physical_index?: number | null
  line_num?: number | null
  enabled?: boolean
  /** Structural tag (e.g. substantive, heading_like, appendix); null/omit clears */
  content_type?: string | null
}

export interface PageIndexChunkDetailResponse {
  chunk: PageIndexChunk
}

export type PageIndexChunkMergeStrategy = 'concatenate' | 'keep_first'

export interface PageIndexChunkDeleteResponse {
  message: string
}

export interface UserMemoryResponse {
  memory: Record<string, {
    title: string
    content: string
    updated_at: string | null
  }>
}

/** Node summary from /api/graph/subgraph and /api/graph/expand */
export interface GraphVizNode {
  id: string
  entity: string
  label: string
  degree: number
  missing?: boolean
  context?: Record<string, unknown>
}

/** Edge summary from progressive graph JSON APIs */
export interface GraphVizEdge {
  id: string
  source: string
  target: string
  bidirectional: boolean
  /** Edge entity/class name (same as label). */
  entity: string
  label: string
  /** Present on /api/graph/expand only: direction relative to expanded node. */
  direction?: 'outgoing' | 'incoming' | 'loop' | 'undirected'
  context?: Record<string, unknown>
}

export interface GraphExpandPagination {
  cursor: number
  next_cursor: number | null
  has_more: boolean
  total_edge_count: number
  returned_edges: number
}

export interface GraphExpandResponse {
  center_id: string
  nodes: GraphVizNode[]
  edges: GraphVizEdge[]
  pagination: GraphExpandPagination
  found: boolean
}

export interface GraphSubgraphMeta {
  max_depth: number
  max_nodes: number
  max_edges_per_node: number
  truncated: boolean
  node_count: number
  edge_count: number
}

export interface GraphSubgraphResponse {
  root_id: string
  nodes: GraphVizNode[]
  edges: GraphVizEdge[]
  meta: GraphSubgraphMeta
}

/** Google Drive file entry (may nest `files` for folders) */
export interface GoogleDriveFileEntry {
  id: string
  name?: string
  mimeType?: string
  createdTime?: string
  modifiedTime?: string
  url?: string
  disable_ingestion?: boolean
  files?: GoogleDriveFileEntry[]
}

export interface GoogleDriveDocQueues {
  added: unknown[]
  modified: unknown[]
  removed: unknown[]
}

export interface GoogleDriveFolderState {
  node_id: string
  document_id: string
  folder_id: string
  /** Display name of the synced Drive folder (from Drive metadata). */
  folder_name?: string
  ingesting_documents: GoogleDriveDocQueues
  failed_documents: GoogleDriveDocQueues
  status: string
  active_document: string
  metadata: Record<string, unknown>
  files: GoogleDriveFileEntry[]
}

export interface GoogleDriveListResponse {
  documents: GoogleDriveFolderState[]
}
