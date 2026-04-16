import { useState, useEffect, useCallback, useMemo } from 'react'
import { apiClient } from '../config/api'
import type {
  GoogleDriveFileEntry,
  GoogleDriveFolderState,
  PageIndexChunk,
  PageIndexChunkMergeStrategy,
  PageIndexChunkUpdatePayload,
  PageIndexDocument,
} from '../types/api'
import { useTheme } from '../context/ThemeContext'

interface PageIndexDocumentsModalProps {
  agentId: string
  onClose: () => void
  isEmbedded?: boolean
}

const CHUNK_PAGE_SIZES = [0, 10, 25, 50, 100] as const

const MERGE_QUEUE_MAX = 20

/** Matches server `documents._TEXT_JOIN` for merge preview. */
const MERGE_TEXT_JOIN = '\n\n---\n\n'

const GOOGLE_DRIVE_FOLDER_MIME = 'application/vnd.google-apps.folder'

function isPageIndexGoogleDriveSyncAction(a: Record<string, unknown>): boolean {
  const entity = String(a.entity ?? '')
  const archetype = String(a.archetype ?? '')
  const action = String(a.action ?? '')
  const label = String(
    (a.context as { label?: string } | undefined)?.label ?? a.label ?? ''
  )
  return (
    entity.includes('PageIndexGoogleDriveSyncAction') ||
    archetype.includes('PageIndexGoogleDriveSyncAction') ||
    action === 'jvagent/pageindex_google_drive_sync_action' ||
    label.includes('pageindex_google_drive_sync')
  )
}

function flattenGoogleDriveFiles(files: GoogleDriveFileEntry[]): GoogleDriveFileEntry[] {
  const out: GoogleDriveFileEntry[] = []
  const walk = (items: GoogleDriveFileEntry[]) => {
    for (const it of items) {
      if (it.mimeType === GOOGLE_DRIVE_FOLDER_MIME && it.files?.length) {
        walk(it.files)
      } else if (it.mimeType !== GOOGLE_DRIVE_FOLDER_MIME) {
        out.push(it)
      }
    }
  }
  walk(files)
  return out
}

function describeDriveQueueItem(item: unknown): { title: string; subtitle: string; url?: string } {
  if (!item || typeof item !== 'object') {
    return { title: String(item), subtitle: '' }
  }
  const o = item as Record<string, unknown>
  if ('new' in o && o.new && typeof o.new === 'object') {
    const nw = o.new as Record<string, unknown>
    const old = o.old as Record<string, unknown> | undefined
    return {
      title: String(nw.name ?? nw.id ?? 'modified'),
      subtitle: old ? `was: ${String(old.name ?? '')}` : String(o.id ?? ''),
      url: typeof nw.url === 'string' ? nw.url : undefined,
    }
  }
  return {
    title: String(o.name ?? o.id ?? '?'),
    subtitle: String(o.mimeType ?? o.id ?? ''),
    url: typeof o.url === 'string' ? o.url : undefined,
  }
}

type ChunkEnabledFilter = 'all' | 'rag_enabled' | 'rag_disabled'

type ChunkSortKey = 'title' | 'content_type' | 'enabled'

const CHUNK_CONTENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '—' },
  { value: 'substantive', label: 'substantive' },
  { value: 'heading_like', label: 'heading_like' },
  { value: 'appendix', label: 'appendix' },
  { value: 'introduction', label: 'introduction' },
  { value: 'empty', label: 'empty' },
  { value: 'table_of_contents', label: 'table_of_contents' },
  { value: 'bibliography', label: 'bibliography' },
  { value: 'foreword', label: 'foreword' },
  { value: 'copyright', label: 'copyright' },
  { value: 'standard_title', label: 'standard_title' },
  { value: 'running_header', label: 'running_header' },
]

function chunkSortComparable(c: PageIndexChunk, key: ChunkSortKey): string | number | boolean | null {
  switch (key) {
    case 'title':
      return (c.title || '').toLowerCase()
    case 'content_type':
      return (c.content_type || '').toLowerCase()
    case 'enabled':
      return c.enabled !== false
    default:
      return ''
  }
}

function compareChunksForSort(
  a: PageIndexChunk,
  b: PageIndexChunk,
  key: ChunkSortKey,
  dir: 'asc' | 'desc'
): number {
  const mult = dir === 'asc' ? 1 : -1
  const va = chunkSortComparable(a, key)
  const vb = chunkSortComparable(b, key)
  const tie = (a.id || '').localeCompare(b.id || '')

  if (typeof va === 'boolean' && typeof vb === 'boolean') {
    if (va === vb) return tie
    return (Number(va) - Number(vb)) * mult
  }
  const sa = String(va)
  const sb = String(vb)
  if (sa === sb) return tie
  return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: 'base' }) * mult
}

function mergeNonemptyStrParts(values: (string | null | undefined)[]): string[] {
  const parts: string[] = []
  for (const v of values) {
    if (v == null) continue
    const s = String(v).trim()
    if (s) parts.push(s)
  }
  return parts
}

function mergeFirstContentType(chunks: PageIndexChunk[]): string | null {
  for (const c of chunks) {
    const ct = c.content_type
    if (ct == null || ct === '') continue
    const s = String(ct).trim()
    if (s) return s
  }
  return null
}

/** Computed baseline for editable merge fields (order + strategy). */
function computeMergePreview(
  queue: PageIndexChunk[],
  strategy: PageIndexChunkMergeStrategy
): {
  title: string
  text: string
  summary: string | null
  prefix_summary: string | null
  enabled: boolean
  content_type: string | null
  sameDocument: boolean
} | null {
  if (queue.length < 2) return null
  const docNames = new Set(queue.map((c) => c.doc_name).filter(Boolean))
  const sameDocument = docNames.size === 1
  const ordered = queue
  const keep = queue[0]
  const rest = queue.slice(1)

  let title: string
  let text: string
  let summary: string | null
  let prefix_summary: string | null

  if (strategy === 'concatenate') {
    title = mergeNonemptyStrParts(ordered.map((c) => c.title)).join(' / ')
    text = mergeNonemptyStrParts(ordered.map((c) => c.text)).join(MERGE_TEXT_JOIN)
    const sumParts = mergeNonemptyStrParts(ordered.map((c) => c.summary ?? null))
    summary = sumParts.length ? sumParts.join(MERGE_TEXT_JOIN) : null
    const prefParts = mergeNonemptyStrParts(ordered.map((c) => c.prefix_summary ?? null))
    prefix_summary = prefParts.length ? prefParts.join(MERGE_TEXT_JOIN) : null
  } else {
    title = (keep.title || '').trim()
    const bodyParts = mergeNonemptyStrParts([keep.text, ...rest.map((c) => c.text)])
    text = bodyParts.join(MERGE_TEXT_JOIN)
    const sumParts = mergeNonemptyStrParts([
      keep.summary ?? null,
      ...rest.map((c) => c.summary ?? null),
    ])
    summary = sumParts.length ? sumParts.join(MERGE_TEXT_JOIN) : null
    const prefParts = mergeNonemptyStrParts([
      keep.prefix_summary ?? null,
      ...rest.map((c) => c.prefix_summary ?? null),
    ])
    prefix_summary = prefParts.length ? prefParts.join(MERGE_TEXT_JOIN) : null
  }

  const enabled = ordered.some((c) => c.enabled !== false)
  const content_type = mergeFirstContentType(ordered)

  return { title, text, summary, prefix_summary, enabled, content_type, sameDocument }
}

type MergeDraft = {
  title: string
  text: string
  summary: string
  prefixSummary: string
  enabled: boolean
  contentType: string
}

export function PageIndexDocumentsModal({
  agentId,
  onClose,
  isEmbedded = true,
}: PageIndexDocumentsModalProps) {
  const { theme } = useTheme()
  const dark = theme === 'dark'

  const [documents, setDocuments] = useState<PageIndexDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [ingestFileUrl, setIngestFileUrl] = useState('')
  const [docName, setDocName] = useState('')
  const [docDescription, setDocDescription] = useState('')
  const [docUrl, setDocUrl] = useState('')
  const [metadataJson, setMetadataJson] = useState('')
  const [addNodeSummary, setAddNodeSummary] = useState(true)
  const [convertToMarkdown, setConvertToMarkdown] = useState(false)
  const [doclingOcr, setDoclingOcr] = useState(false)
  const [purgeOnImport, setPurgeOnImport] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importText, setImportText] = useState('')
  const [importUrl, setImportUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportCollectionName, setExportCollectionName] = useState(agentId)
  const [exportRootId, setExportRootId] = useState('')
  const [importExportError, setImportExportError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<
    'import-export' | 'documents' | 'chunks' | 'google-sync'
  >('documents')

  const [driveSyncActionId, setDriveSyncActionId] = useState<string | null>(null)
  const [driveFolders, setDriveFolders] = useState<GoogleDriveFolderState[]>([])
  const [driveSelectedFolderId, setDriveSelectedFolderId] = useState('')
  const [driveLoading, setDriveLoading] = useState(false)
  const [driveError, setDriveError] = useState<string | null>(null)
  const [driveRetrying, setDriveRetrying] = useState(false)
  const [driveDeleting, setDriveDeleting] = useState(false)
  const [driveTogglingFileId, setDriveTogglingFileId] = useState<string | null>(null)
  const [driveIngestConvertMd, setDriveIngestConvertMd] = useState(false)
  const [driveIngestOcr, setDriveIngestOcr] = useState(false)
  const [driveRemoveDeleted, setDriveRemoveDeleted] = useState(false)

  const [chunksDocName, setChunksDocName] = useState('')
  const [chunkEnabledFilter, setChunkEnabledFilter] = useState<ChunkEnabledFilter>('all')
  const [chunkFilterInput, setChunkFilterInput] = useState('')
  const [chunkFilterQ, setChunkFilterQ] = useState('')
  const [chunksPerPage, setChunksPerPage] = useState<number>(0)
  const [chunksPage, setChunksPage] = useState(1)
  const [chunks, setChunks] = useState<PageIndexChunk[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [chunksError, setChunksError] = useState<string | null>(null)
  const [editingChunk, setEditingChunk] = useState<PageIndexChunk | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editText, setEditText] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [editRootMetadataJson, setEditRootMetadataJson] = useState('')
  const [initialRootMetadataJson, setInitialRootMetadataJson] = useState('')
  const [savingChunk, setSavingChunk] = useState(false)
  const [saveChunkError, setSaveChunkError] = useState<string | null>(null)
  const [deletingChunkId, setDeletingChunkId] = useState<string | null>(null)
  const [editEnabled, setEditEnabled] = useState(true)
  const [editContentType, setEditContentType] = useState('')
  const [chunkSort, setChunkSort] = useState<{
    key: ChunkSortKey
    dir: 'asc' | 'desc'
  }>({ key: 'title', dir: 'asc' })
  const [quickSavingChunkId, setQuickSavingChunkId] = useState<string | null>(null)
  const [mergeQueue, setMergeQueue] = useState<PageIndexChunk[]>([])
  const [mergeStrategy, setMergeStrategy] =
    useState<PageIndexChunkMergeStrategy>('concatenate')
  const [mergingChunks, setMergingChunks] = useState(false)
  const [mergeDraft, setMergeDraft] = useState<MergeDraft | null>(null)
  const [applyMergeUpdate, setApplyMergeUpdate] = useState(true)
  const [applyMergeDeleteOthers, setApplyMergeDeleteOthers] = useState(true)

  const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50MB

  const parseMetadata = (): Record<string, unknown> | undefined => {
    const trimmed = metadataJson.trim()
    if (!trimmed) return undefined
    try {
      const parsed = JSON.parse(trimmed)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : undefined
    } catch {
      return undefined
    }
  }

  const fetchDocuments = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiClient.listPageIndexDocuments(agentId)
      setDocuments(res.documents || [])
    } catch (err: any) {
      console.error('Failed to fetch documents:', err)
      setError(err.message || 'Failed to load documents')
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    fetchDocuments()
  }, [fetchDocuments])

  useEffect(() => {
    setMergeQueue([])
    setMergeStrategy('concatenate')
    setMergeDraft(null)
    setApplyMergeUpdate(true)
    setApplyMergeDeleteOthers(true)
  }, [agentId])

  useEffect(() => {
    if (mergeQueue.length < 2) {
      setMergeDraft(null)
      return
    }
    const baseline = computeMergePreview(mergeQueue, mergeStrategy)
    if (!baseline) {
      setMergeDraft(null)
      return
    }
    setMergeDraft({
      title: baseline.title,
      text: baseline.text,
      summary: baseline.summary ?? '',
      prefixSummary: baseline.prefix_summary ?? '',
      enabled: baseline.enabled,
      contentType: baseline.content_type ?? '',
    })
  }, [mergeQueue, mergeStrategy])

  useEffect(() => {
    const t = window.setTimeout(() => setChunkFilterQ(chunkFilterInput), 300)
    return () => window.clearTimeout(t)
  }, [chunkFilterInput])

  useEffect(() => {
    if (
      chunksDocName &&
      documents.length > 0 &&
      !documents.some((d) => d.doc_name === chunksDocName)
    ) {
      setChunksDocName('')
    }
  }, [documents, chunksDocName])

  useEffect(() => {
    setChunksPage(1)
  }, [chunksDocName, chunkFilterQ, chunksPerPage, chunkEnabledFilter])

  const fetchChunks = useCallback(async () => {
    if (activeTab !== 'chunks') return
    setChunksLoading(true)
    setChunksError(null)
    try {
      const chunk_enabled =
        chunkEnabledFilter === 'rag_enabled'
          ? 'true'
          : chunkEnabledFilter === 'rag_disabled'
            ? 'false'
            : undefined
      const params = {
        page: chunksPerPage === 0 ? 1 : chunksPage,
        per_page: chunksPerPage,
        q: chunkFilterQ.trim() || undefined,
        chunk_enabled,
      }
      const res = chunksDocName
        ? await apiClient.listPageIndexChunks(agentId, chunksDocName, params)
        : await apiClient.listPageIndexChunksForCollection(agentId, params)
      setChunks(res.chunks || [])
      setChunksTotal(typeof res.total === 'number' ? res.total : 0)
    } catch (err: any) {
      console.error('Failed to fetch chunks:', err)
      setChunksError(err.message || 'Failed to load chunks')
      setChunks([])
      setChunksTotal(0)
    } finally {
      setChunksLoading(false)
    }
  }, [
    agentId,
    chunksDocName,
    activeTab,
    chunksPage,
    chunksPerPage,
    chunkFilterQ,
    chunkEnabledFilter,
  ])

  useEffect(() => {
    fetchChunks()
  }, [fetchChunks])

  useEffect(() => {
    if (!isEmbedded || !onClose) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [isEmbedded, onClose])

  useEffect(() => {
    setExportCollectionName(agentId)
    setExportRootId('')
  }, [agentId])

  const refreshGoogleDriveList = useCallback(async () => {
    if (!driveSyncActionId) return
    try {
      const docRes = await apiClient.listGoogleDriveDocuments(driveSyncActionId)
      setDriveFolders(docRes.documents)
    } catch (e: unknown) {
      setDriveError(e instanceof Error ? e.message : 'Failed to refresh Google Sync')
    }
  }, [driveSyncActionId])

  useEffect(() => {
    if (activeTab !== 'google-sync') return
    let cancelled = false
    ;(async () => {
      setDriveLoading(true)
      setDriveError(null)
      try {
        const res = await apiClient.getActions(agentId, { page: 1, per_page: 100 })
        const list = res?.actions ?? res?.data?.actions ?? res ?? []
        const arr = Array.isArray(list) ? list : []
        const found = arr.find((x: Record<string, unknown>) =>
          isPageIndexGoogleDriveSyncAction(x)
        ) as { id?: string } | undefined
        const aid = found?.id ? String(found.id) : null
        if (cancelled) return
        setDriveSyncActionId(aid)
        if (!aid) {
          setDriveFolders([])
          setDriveSelectedFolderId('')
          setDriveLoading(false)
          return
        }
        const docRes = await apiClient.listGoogleDriveDocuments(aid)
        if (cancelled) return
        setDriveFolders(docRes.documents)
        setDriveSelectedFolderId((prev) => {
          if (prev && docRes.documents.some((d) => d.folder_id === prev)) return prev
          return docRes.documents[0]?.folder_id ?? ''
        })
      } catch (e: unknown) {
        if (!cancelled) {
          setDriveError(
            e instanceof Error ? e.message : 'Failed to load Google Sync'
          )
        }
      } finally {
        if (!cancelled) setDriveLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeTab, agentId])

  const normalizeMarkdownForUpload = async (file: File): Promise<File> => {
    const ext = file.name.toLowerCase().slice(file.name.lastIndexOf('.'))
    if (ext !== '.md' && ext !== '.markdown') return file
    const buf = await file.arrayBuffer()
    const decoder = new TextDecoder('utf-8', { fatal: false })
    const text = decoder.decode(buf)
    const encoder = new TextEncoder()
    const clean = encoder.encode(text)
    return new File([clean], file.name, { type: 'application/octet-stream' })
  }

  const handleUpload = async () => {
    const remoteUrl = ingestFileUrl.trim()
    if (!selectedFile && !remoteUrl) return
    if (selectedFile && remoteUrl) {
      setUploadError('Choose a file or a document URL, not both')
      return
    }

    if (selectedFile && selectedFile.size > MAX_FILE_SIZE) {
      setUploadError(`File size exceeds ${MAX_FILE_SIZE / (1024 * 1024)}MB limit`)
      return
    }

    setUploading(true)
    setUploadError(null)
    try {
      const opts = {
        docName: docName || undefined,
        docDescription: docDescription || undefined,
        docUrl: docUrl || undefined,
        metadata: parseMetadata(),
        ifAddNodeSummary: addNodeSummary,
        convertToMarkdown,
        ocr: doclingOcr,
      }
      if (remoteUrl) {
        await apiClient.uploadPageIndexDocument(agentId, null, {
          ...opts,
          fileUrl: remoteUrl,
        })
        setIngestFileUrl('')
      } else {
        const fileToUpload = await normalizeMarkdownForUpload(selectedFile!)
        await apiClient.uploadPageIndexDocument(agentId, fileToUpload, opts)
        setSelectedFile(null)
      }
      setDocName('')
      setDocDescription('')
      setDocUrl('')
      setMetadataJson('')
      await fetchDocuments()
    } catch (err: any) {
      console.error('Upload failed:', err)
      const errorMsg = err.message || 'Upload failed'
      setUploadError(
        errorMsg.includes('timeout')
          ? 'Upload timed out. File may be too large or server is slow.'
          : errorMsg
      )
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (name: string) => {
    setDeleting(name)
    try {
      await apiClient.deletePageIndexDocument(agentId, name)
      await fetchDocuments()
    } catch (err: any) {
      console.error('Delete failed:', err)
      setError(err.message || 'Delete failed')
    } finally {
      setDeleting(null)
    }
  }

  const handleExport = async () => {
    setExporting(true)
    setImportExportError(null)
    try {
      const data = await apiClient.exportPageIndex(
        'json',
        exportCollectionName,
        exportRootId || undefined
      )
      const selectedDoc = exportRootId
        ? documents.find((d) => d.root_id === exportRootId)
        : undefined
      // 1. Get the raw name first
      let namePart = selectedDoc?.doc_name || (exportRootId ? exportRootId.split('.').pop() : 'all');

      // 2. STRIP THE EXTENSION FIRST (Before cleaning/slicing)
      // This ensures we don't accidentally slice off the "." we need to find the extension.
      if (namePart && namePart.includes('.')) {
          namePart = namePart.split('.').slice(0, -1).join('.');
      }

      // 3. CLEAN THE FILENAME (Sanitize special characters)
      const cleanName = (namePart ?? 'export')
        .replace(/[^a-zA-Z0-9._-]+/g, '_')
        .replace(/_+/g, '_')
        .slice(0, 48);                    // Limit length for OS compatibility


      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${cleanName || 'export'}.json`;
      a.click()
      URL.revokeObjectURL(url)
    } catch (err: any) {
      console.error('Export failed:', err)
      setImportExportError(err.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const handleImport = async () => {
    const url = importUrl.trim()
    let data: unknown
    if (!url) {
      const source = importText.trim() || (importFile ? await importFile.text() : '')
      if (!source) return
      try {
        data = JSON.parse(source)
      } catch {
        setImportExportError('Invalid JSON')
        return
      }
    }
    setImporting(true)
    setImportExportError(null)
    try {
      if (url) {
        await apiClient.importPageIndex(agentId, {
          importUrl: url,
          purge: purgeOnImport,
        })
        setImportUrl('')
      } else {
        await apiClient.importPageIndex(agentId, {
          data,
          purge: purgeOnImport,
        })
      }
      setImportFile(null)
      setImportText('')
      setPurgeOnImport(false)
      await fetchDocuments()
    } catch (err: any) {
      console.error('Import failed:', err)
      setImportExportError(err.message || 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const openEditChunk = (c: PageIndexChunk) => {
    setEditingChunk(c)
    setEditTitle(c.title ?? '')
    setEditText(c.text ?? '')
    setEditSummary(c.summary ?? '')
    setEditEnabled(c.enabled !== false)
    setEditContentType(c.content_type ?? '')
    const docRow = documents.find((d) => d.doc_name === c.doc_name)
    const meta = docRow?.metadata
    const metaStr =
      meta != null && typeof meta === 'object' && Object.keys(meta).length > 0
        ? JSON.stringify(meta, null, 2)
        : '{}'
    setEditRootMetadataJson(metaStr)
    setInitialRootMetadataJson(metaStr)
    setSaveChunkError(null)
  }

  const closeEditChunk = () => {
    setEditingChunk(null)
    setSaveChunkError(null)
  }

  const normalizeJsonForCompare = (raw: string): string => {
    const t = raw.trim() || '{}'
    const p = JSON.parse(t)
    return JSON.stringify(p)
  }

  const handleSaveChunk = async () => {
    if (!editingChunk) return
    const chunkDocName = editingChunk.doc_name
    if (!chunkDocName) {
      setSaveChunkError('Chunk has no document name')
      return
    }
    let parsedMeta: Record<string, unknown> | null
    try {
      const trimmed = editRootMetadataJson.trim() || '{}'
      const p = JSON.parse(trimmed)
      if (p === null) {
        parsedMeta = null
      } else if (typeof p === 'object' && !Array.isArray(p)) {
        parsedMeta = p as Record<string, unknown>
      } else {
        setSaveChunkError('Metadata must be a JSON object or null')
        return
      }
    } catch {
      setSaveChunkError('Invalid metadata JSON')
      return
    }
    setSavingChunk(true)
    setSaveChunkError(null)
    try {
      await apiClient.updatePageIndexChunk(agentId, chunkDocName, editingChunk.id, {
        title: editTitle,
        text: editText,
        summary: editSummary || null,
        enabled: editEnabled,
        content_type: editContentType.trim() ? editContentType.trim() : null,
      })
      const metaChanged =
        normalizeJsonForCompare(editRootMetadataJson) !==
        normalizeJsonForCompare(initialRootMetadataJson)
      if (metaChanged) {
        await apiClient.patchPageIndexDocumentMetadata(agentId, chunkDocName, parsedMeta)
        await fetchDocuments()
      }
      closeEditChunk()
      await fetchChunks()
    } catch (err: any) {
      console.error('Chunk update failed:', err)
      setSaveChunkError(err.message || 'Update failed')
    } finally {
      setSavingChunk(false)
    }
  }

  const handleDeleteChunk = async (c: PageIndexChunk) => {
    if (!c.doc_name) return
    const ok = window.confirm(
      `Delete this chunk${c.title ? ` (“${c.title.slice(0, 80)}”)` : ''} and its nested sections? This cannot be undone.`
    )
    if (!ok) return
    setDeletingChunkId(c.id)
    try {
      await apiClient.deletePageIndexChunk(agentId, c.doc_name, c.id, { cascade: true })
      if (editingChunk?.id === c.id) closeEditChunk()
      setMergeQueue((q) => q.filter((x) => x.id !== c.id))
      await fetchChunks()
    } catch (err: any) {
      console.error('Chunk delete failed:', err)
      setChunksError(err.message || 'Delete failed')
    } finally {
      setDeletingChunkId(null)
    }
  }

  const truncate = (s: string, n: number) => {
    if (!s) return '—'
    return s.length <= n ? s : `${s.slice(0, n)}…`
  }

  const totalChunkPages =
    chunksPerPage > 0 ? Math.max(1, Math.ceil(chunksTotal / chunksPerPage)) : 1

  const sortedChunks = useMemo(() => {
    const copy = [...chunks]
    copy.sort((a, b) => compareChunksForSort(a, b, chunkSort.key, chunkSort.dir))
    return copy
  }, [chunks, chunkSort.key, chunkSort.dir])

  const mergeQueueIds = useMemo(() => new Set(mergeQueue.map((c) => c.id)), [mergeQueue])

  const tableChunks = useMemo(
    () => sortedChunks.filter((c) => !mergeQueueIds.has(c.id)),
    [sortedChunks, mergeQueueIds]
  )

  const mergeSameDocument = useMemo(() => {
    if (mergeQueue.length < 2) return true
    const docNames = new Set(mergeQueue.map((c) => c.doc_name).filter(Boolean))
    return docNames.size === 1
  }, [mergeQueue])

  const mergeFieldsLabelClass = dark ? 'text-xs text-slate-400' : 'text-xs text-gray-600'

  const mergeTextareaClass = dark
    ? 'w-full mt-0.5 px-2 py-1.5 text-sm rounded border border-slate-600 bg-slate-800 text-slate-100 resize-y min-h-[4rem] max-h-48'
    : 'w-full mt-0.5 px-2 py-1.5 text-sm rounded border border-gray-300 bg-white text-gray-900 resize-y min-h-[4rem] max-h-48'

  const toggleChunkSort = (key: ChunkSortKey) => {
    setChunkSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'asc' }
    )
  }

  const handleChunkQuickPatch = async (c: PageIndexChunk, patch: PageIndexChunkUpdatePayload) => {
    if (!c.doc_name) return
    setQuickSavingChunkId(c.id)
    setChunksError(null)
    try {
      await apiClient.updatePageIndexChunk(agentId, c.doc_name, c.id, patch)
      await fetchChunks()
    } catch (err: any) {
      console.error('Chunk quick update failed:', err)
      setChunksError(err.message || 'Update failed')
    } finally {
      setQuickSavingChunkId(null)
    }
  }

  const addToMergeQueue = (c: PageIndexChunk) => {
    setMergeQueue((prev) => {
      if (prev.some((x) => x.id === c.id)) return prev
      if (prev.length >= MERGE_QUEUE_MAX) {
        setChunksError(`Merge queue limited to ${MERGE_QUEUE_MAX} chunks.`)
        return prev
      }
      setChunksError(null)
      return [...prev, c]
    })
  }

  const removeFromMergeQueue = (chunkId: string) => {
    setMergeQueue((prev) => prev.filter((x) => x.id !== chunkId))
  }

  const clearMergeQueue = () => {
    setMergeQueue([])
    setChunksError(null)
  }

  const moveMergeQueueItem = (index: number, delta: -1 | 1) => {
    setMergeQueue((prev) => {
      const j = index + delta
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      const tmp = next[index]
      next[index] = next[j]
      next[j] = tmp
      return next
    })
  }

  const handleMergeChunks = async () => {
    if (mergeQueue.length < 2 || mergingChunks) return
    if (!applyMergeUpdate && !applyMergeDeleteOthers) return
    if (applyMergeUpdate && !mergeDraft) return

    const docNames = new Set(mergeQueue.map((c) => c.doc_name).filter(Boolean))
    if (docNames.size !== 1) {
      setChunksError('All chunks in the merge list must belong to the same document.')
      return
    }
    const doc = mergeQueue[0].doc_name
    if (!doc) {
      setChunksError('Each chunk must have a document name to merge.')
      return
    }
    const keepId = mergeQueue[0].id

    setMergingChunks(true)
    setChunksError(null)
    try {
      if (applyMergeUpdate && mergeDraft) {
        await apiClient.updatePageIndexChunk(agentId, doc, keepId, {
          title: mergeDraft.title,
          text: mergeDraft.text,
          summary: mergeDraft.summary.trim() ? mergeDraft.summary : null,
          prefix_summary: mergeDraft.prefixSummary.trim() ? mergeDraft.prefixSummary : null,
          enabled: mergeDraft.enabled,
          content_type: mergeDraft.contentType.trim() ? mergeDraft.contentType.trim() : null,
        })
      }
      if (applyMergeDeleteOthers) {
        for (const c of mergeQueue.slice(1)) {
          await apiClient.deletePageIndexChunk(agentId, doc, c.id, { cascade: false })
        }
      }
      setMergeQueue([])
      setApplyMergeUpdate(true)
      setApplyMergeDeleteOthers(true)
      await fetchChunks()
    } catch (err: unknown) {
      console.error('Chunk merge failed:', err)
      const msg = err instanceof Error ? err.message : 'Merge failed'
      setChunksError(msg)
      await fetchChunks()
    } finally {
      setMergingChunks(false)
    }
  }

  const selectedDriveFolder = useMemo(
    () => driveFolders.find((f) => f.folder_id === driveSelectedFolderId) ?? null,
    [driveFolders, driveSelectedFolderId]
  )

  const handleDriveRetryFailed = async () => {
    if (!driveSyncActionId || !selectedDriveFolder) return
    setDriveRetrying(true)
    setDriveError(null)
    try {
      await apiClient.ingestGoogleDocuments(driveSyncActionId, {
        google_drive_folders: [
          {
            folder_id: selectedDriveFolder.folder_id,
            metadata: selectedDriveFolder.metadata ?? {},
          },
        ],
        retry_failed_documents: true,
        remove_deleted_documents: driveRemoveDeleted,
        convert_to_markdown: driveIngestConvertMd,
        ocr: driveIngestOcr,
      })
      await refreshGoogleDriveList()
    } catch (e: unknown) {
      setDriveError(e instanceof Error ? e.message : 'Retry failed')
    } finally {
      setDriveRetrying(false)
    }
  }

  const handleDriveIngestOnce = async () => {
    if (!driveSyncActionId || !selectedDriveFolder) return
    setDriveRetrying(true)
    setDriveError(null)
    try {
      await apiClient.ingestGoogleDocuments(driveSyncActionId, {
        google_drive_folders: [
          {
            folder_id: selectedDriveFolder.folder_id,
            metadata: selectedDriveFolder.metadata ?? {},
          },
        ],
        retry_failed_documents: false,
        remove_deleted_documents: driveRemoveDeleted,
        convert_to_markdown: driveIngestConvertMd,
        ocr: driveIngestOcr,
      })
      await refreshGoogleDriveList()
    } catch (e: unknown) {
      setDriveError(e instanceof Error ? e.message : 'Ingest failed')
    } finally {
      setDriveRetrying(false)
    }
  }

  const handleDriveDeleteFolder = async () => {
    if (!driveSyncActionId || !selectedDriveFolder) return
    const fn = selectedDriveFolder.folder_name?.trim()
    const folderLabel =
      fn && fn !== selectedDriveFolder.folder_id
        ? `${fn} (${selectedDriveFolder.folder_id})`
        : selectedDriveFolder.folder_id
    if (
      !window.confirm(
        `Remove Google Drive sync tracking for "${folderLabel}"? Indexed PageIndex documents are not removed.`
      )
    )
      return
    setDriveDeleting(true)
    setDriveError(null)
    try {
      await apiClient.deleteGoogleDriveDocuments(driveSyncActionId, {
        document_id: selectedDriveFolder.document_id ?? selectedDriveFolder.folder_id,
      })
      await refreshGoogleDriveList()
    } catch (e: unknown) {
      setDriveError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDriveDeleting(false)
    }
  }

  const handleDriveToggleDisable = async (file: GoogleDriveFileEntry, next: boolean) => {
    if (!driveSyncActionId || !selectedDriveFolder) return
    setDriveTogglingFileId(file.id)
    setDriveError(null)
    try {
      await apiClient.setGoogleDriveFileIngestion(driveSyncActionId, {
        folder_id: selectedDriveFolder.folder_id,
        file_id: file.id,
        disable_ingestion: next,
      })
      await refreshGoogleDriveList()
    } catch (e: unknown) {
      setDriveError(e instanceof Error ? e.message : 'Update failed')
    } finally {
      setDriveTogglingFileId(null)
    }
  }

  const inputClass = dark
    ? 'w-full px-3 py-2 border border-slate-600 rounded-lg text-sm bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500'
    : 'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white text-gray-900 placeholder-gray-500 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500'

  const labelClass = dark ? 'text-xs text-slate-400' : 'text-xs text-gray-600'

  const chunkSortBtnClass = `inline-flex items-center gap-1 w-full text-left uppercase font-medium ${
    dark ? 'text-slate-400 hover:text-slate-200' : 'text-gray-500 hover:text-gray-800'
  }`

  const chunkThClass = (extra = '') =>
    `px-3 py-2 text-left text-xs ${dark ? 'text-slate-400' : 'text-gray-500'} ${extra}`

  const chunkSortCaret = (key: ChunkSortKey) =>
    chunkSort.key === key ? (chunkSort.dir === 'asc' ? ' ▲' : ' ▼') : ''

  const content = (
    <div
      className={`rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col border ${
        dark ? 'bg-slate-900 border-slate-700 text-slate-100' : 'bg-white border-gray-200 text-gray-900'
      }`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      <div
        className={`flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${
          dark ? 'border-slate-700' : 'border-gray-200'
        }`}
      >
        <h2 className={`text-xl sm:text-2xl font-semibold ${dark ? 'text-slate-100' : 'text-gray-900'}`}>
          Documents
        </h2>
        <button
          onClick={onClose}
          className={`p-2 rounded-lg transition-colors ${
            dark
              ? 'text-gray-400 hover:text-gray-100 hover:bg-gray-700'
              : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
          }`}
          aria-label="Close"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className={`flex-shrink-0 border-b ${dark ? 'border-slate-700' : 'border-gray-200'}`}>
        <nav className="flex flex-wrap gap-1 px-4 sm:px-6" aria-label="Tabs">
          <button
            type="button"
            onClick={() => setActiveTab('documents')}
            className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === 'documents'
                ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                : `border-transparent ${
                    dark
                      ? 'text-slate-400 hover:text-slate-300 hover:border-slate-600'
                      : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`
            }`}
          >
            Upload & List
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('chunks')}
            className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === 'chunks'
                ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                : `border-transparent ${
                    dark
                      ? 'text-slate-400 hover:text-slate-300 hover:border-slate-600'
                      : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`
            }`}
          >
            Chunks
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('import-export')}
            className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === 'import-export'
                ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                : `border-transparent ${
                    dark
                      ? 'text-slate-400 hover:text-slate-300 hover:border-slate-600'
                      : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`
            }`}
          >
            Import / Export
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('google-sync')}
            className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === 'google-sync'
                ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                : `border-transparent ${
                    dark
                      ? 'text-slate-400 hover:text-slate-300 hover:border-slate-600'
                      : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`
            }`}
          >
            Google Sync
          </button>
        </nav>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-4">
        {activeTab === 'google-sync' && (
          <div className="space-y-6">
            {driveLoading && driveFolders.length === 0 && !driveError && (
              <p className={`text-sm ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
                Loading Google Drive sync…
              </p>
            )}
            {driveError && (
              <p className="text-sm text-red-600 dark:text-red-400">{driveError}</p>
            )}
            {!driveLoading && !driveSyncActionId && (
              <p className={`text-sm ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
                No PageIndex Google Drive Sync action is attached to this agent.
              </p>
            )}
            {driveSyncActionId && (
              <>
                <div className="flex flex-col sm:flex-row gap-3 sm:items-end flex-wrap">
                  <div className="flex-1 min-w-[220px]">
                    <label className={`block ${labelClass} mb-1`}>Synced folder</label>
                    <select
                      value={driveSelectedFolderId}
                      onChange={(e) => setDriveSelectedFolderId(e.target.value)}
                      className={inputClass}
                      disabled={driveFolders.length === 0}
                    >
                      {driveFolders.length === 0 ? (
                        <option value="">No folders tracked yet</option>
                      ) : (
                        driveFolders.map((f) => {
                          const label = f.folder_name?.trim()
                          const text =
                            label && label !== f.folder_id
                              ? `${label} (${f.folder_id})`
                              : f.folder_id
                          return (
                            <option key={f.folder_id} value={f.folder_id}>
                              {text}
                            </option>
                          )
                        })
                      )}
                    </select>
                  </div>
                  <button
                    type="button"
                    onClick={() => void refreshGoogleDriveList()}
                    disabled={driveLoading}
                    className={`px-3 py-2 text-sm rounded-lg border ${
                      dark
                        ? 'border-slate-600 text-slate-200 hover:bg-slate-800'
                        : 'border-gray-300 text-gray-800 hover:bg-gray-50'
                    } disabled:opacity-50`}
                  >
                    Refresh
                  </button>
                </div>

                {selectedDriveFolder && (
                  <>
                    <div
                      className={`grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm rounded-lg border p-3 ${
                        dark ? 'border-slate-600 bg-slate-800/50' : 'border-gray-200 bg-gray-50'
                      }`}
                    >
                      <div className="sm:col-span-2">
                        <span className={labelClass}>Folder</span>
                        <p className="font-medium break-all">
                          {(selectedDriveFolder.folder_name &&
                            selectedDriveFolder.folder_name.trim()) ||
                            '—'}
                          {selectedDriveFolder.folder_id ? (
                            <span
                              className={`block text-xs font-normal mt-0.5 ${dark ? 'text-slate-400' : 'text-gray-600'}`}
                            >
                              {selectedDriveFolder.folder_id}
                            </span>
                          ) : null}
                        </p>
                      </div>
                      <div>
                        <span className={labelClass}>Status</span>
                        <p className="font-medium">{selectedDriveFolder.status}</p>
                      </div>
                      <div>
                        <span className={labelClass}>Active document</span>
                        <p className="font-medium break-all">
                          {selectedDriveFolder.active_document || '—'}
                        </p>
                      </div>
                      <div className="sm:col-span-2">
                        <span className={labelClass}>Metadata</span>
                        <pre
                          className={`mt-1 text-xs overflow-x-auto p-2 rounded ${
                            dark ? 'bg-slate-900 text-slate-300' : 'bg-white text-gray-800'
                          }`}
                        >
                          {JSON.stringify(selectedDriveFolder.metadata ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                      <div className="space-y-4">
                        <h3
                          className={`text-sm font-semibold ${dark ? 'text-slate-200' : 'text-gray-800'}`}
                        >
                          Ingesting
                        </h3>
                        {(['added', 'modified', 'removed'] as const).map((key) => (
                          <div key={key} className="space-y-2">
                            <h4 className={`text-xs font-semibold uppercase ${labelClass}`}>
                              {key}
                            </h4>
                            {(selectedDriveFolder.ingesting_documents[key] ?? []).length ===
                            0 ? (
                              <p
                                className={`text-sm ${dark ? 'text-slate-500' : 'text-gray-500'}`}
                              >
                                —
                              </p>
                            ) : (
                              <ul className="space-y-2">
                                {(
                                  selectedDriveFolder.ingesting_documents[key] as unknown[]
                                ).map((item, i) => {
                                  const { title, subtitle, url } =
                                    describeDriveQueueItem(item)
                                  return (
                                    <li
                                      key={`ing-${key}-${i}-${title}`}
                                      className={`text-sm rounded border px-2 py-1.5 ${
                                        dark ? 'border-slate-600' : 'border-gray-200'
                                      }`}
                                    >
                                      <div className="font-medium break-all">{title}</div>
                                      <div
                                        className={`text-xs break-all ${dark ? 'text-slate-400' : 'text-gray-600'}`}
                                      >
                                        {subtitle}
                                      </div>
                                      {url && (
                                        <a
                                          href={url}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="text-xs text-indigo-500 hover:underline"
                                        >
                                          Open in Drive
                                        </a>
                                      )}
                                    </li>
                                  )
                                })}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                      <div className="space-y-4">
                        <h3
                          className={`text-sm font-semibold ${dark ? 'text-slate-200' : 'text-gray-800'}`}
                        >
                          Failed
                        </h3>
                        {(['added', 'modified', 'removed'] as const).map((key) => (
                          <div key={`fail-${key}`} className="space-y-2">
                            <h4 className={`text-xs font-semibold uppercase ${labelClass}`}>
                              {key}
                            </h4>
                            {(selectedDriveFolder.failed_documents[key] ?? []).length ===
                            0 ? (
                              <p
                                className={`text-sm ${dark ? 'text-slate-500' : 'text-gray-500'}`}
                              >
                                —
                              </p>
                            ) : (
                              <ul className="space-y-2">
                                {(selectedDriveFolder.failed_documents[key] as unknown[]).map(
                                  (item, i) => {
                                    const { title, subtitle, url } =
                                      describeDriveQueueItem(item)
                                    return (
                                      <li
                                        key={`fd-${key}-${i}-${title}`}
                                        className={`text-sm rounded border px-2 py-1.5 ${
                                          dark ? 'border-slate-600' : 'border-gray-200'
                                        }`}
                                      >
                                        <div className="font-medium break-all">{title}</div>
                                        <div
                                          className={`text-xs break-all ${dark ? 'text-slate-400' : 'text-gray-600'}`}
                                        >
                                          {subtitle}
                                        </div>
                                        {url && (
                                          <a
                                            href={url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="text-xs text-indigo-500 hover:underline"
                                          >
                                            Open in Drive
                                          </a>
                                        )}
                                      </li>
                                    )
                                  }
                                )}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-4 items-center">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={driveIngestConvertMd}
                          onChange={(e) => setDriveIngestConvertMd(e.target.checked)}
                          className="rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                        />
                        <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                          Convert to Markdown
                        </span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={driveIngestOcr}
                          onChange={(e) => setDriveIngestOcr(e.target.checked)}
                          className="rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                        />
                        <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                          OCR (Docling)
                        </span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={driveRemoveDeleted}
                          onChange={(e) => setDriveRemoveDeleted(e.target.checked)}
                          className="rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                        />
                        <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                          Remove deleted from index
                        </span>
                      </label>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void handleDriveIngestOnce()}
                        disabled={driveRetrying || driveDeleting}
                        className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {driveRetrying ? 'Running…' : 'Run ingest (once)'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDriveRetryFailed()}
                        disabled={driveRetrying || driveDeleting}
                        className="px-4 py-2 bg-amber-600 text-white text-sm font-medium rounded-lg hover:bg-amber-700 disabled:opacity-50"
                      >
                        {driveRetrying ? 'Running…' : 'Retry failed'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDriveDeleteFolder()}
                        disabled={driveRetrying || driveDeleting}
                        className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 disabled:opacity-50"
                      >
                        {driveDeleting ? 'Removing…' : 'Remove folder sync'}
                      </button>
                    </div>

                    <div className="space-y-2">
                      <h3
                        className={`text-sm font-semibold ${dark ? 'text-slate-200' : 'text-gray-800'}`}
                      >
                        Files in folder
                      </h3>
                      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-slate-600">
                        <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-600">
                          <thead className={dark ? 'bg-slate-800' : 'bg-gray-50'}>
                            <tr>
                              <th className="px-3 py-2 text-left text-xs font-medium uppercase">
                                Name
                              </th>
                              <th className="px-3 py-2 text-left text-xs font-medium uppercase hidden sm:table-cell">
                                Type
                              </th>
                              <th className="px-3 py-2 text-left text-xs font-medium uppercase">
                                Link
                              </th>
                              <th className="px-3 py-2 text-left text-xs font-medium uppercase">
                                Skip ingest
                              </th>
                            </tr>
                          </thead>
                          <tbody
                            className={`divide-y ${dark ? 'divide-slate-600' : 'divide-gray-200'}`}
                          >
                            {flattenGoogleDriveFiles(selectedDriveFolder.files ?? []).map(
                              (f) => (
                                <tr key={f.id}>
                                  <td className="px-3 py-2 text-sm max-w-[200px] truncate">
                                    {f.name ?? f.id}
                                  </td>
                                  <td className="px-3 py-2 text-sm hidden sm:table-cell">
                                    {f.mimeType ?? '—'}
                                  </td>
                                  <td className="px-3 py-2 text-sm">
                                    {f.url ? (
                                      <a
                                        href={f.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-indigo-500 hover:underline text-xs"
                                      >
                                        Drive
                                      </a>
                                    ) : (
                                      '—'
                                    )}
                                  </td>
                                  <td className="px-3 py-2 text-sm">
                                    <label className="inline-flex items-center gap-2 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        checked={!!f.disable_ingestion}
                                        disabled={driveTogglingFileId === f.id}
                                        onChange={(e) =>
                                          void handleDriveToggleDisable(f, e.target.checked)
                                        }
                                        className="rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                                      />
                                      {driveTogglingFileId === f.id ? '…' : ''}
                                    </label>
                                  </td>
                                </tr>
                              )
                            )}
                          </tbody>
                        </table>
                      </div>
                      {flattenGoogleDriveFiles(selectedDriveFolder.files ?? []).length ===
                        0 && (
                        <p className={`text-sm ${dark ? 'text-slate-500' : 'text-gray-500'}`}>
                          No files in this folder snapshot.
                        </p>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        )}

        {activeTab === 'import-export' && (
          <div className="space-y-6">
            <div className="flex flex-col sm:flex-row gap-3 items-end flex-wrap">
              <div className="flex-1 min-w-[200px]">
                <label className={`block ${labelClass} mb-1`}>Collection / agent ID</label>
                <input
                  type="text"
                  value={exportCollectionName}
                  onChange={(e) => setExportCollectionName(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className={`block ${labelClass} mb-1`}>Export scope</label>
                <select
                  value={exportRootId}
                  onChange={(e) => setExportRootId(e.target.value)}
                  className={inputClass}
                  aria-label="Export all documents or one document by root id"
                >
                  <option value="">All documents</option>
                  {documents.map((d) => (
                    <option key={d.root_id} value={d.root_id}>
                      {d.doc_name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                onClick={handleExport}
                disabled={exporting}
                className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 dark:bg-green-600 dark:hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0"
              >
                {exporting ? 'Exporting...' : 'Export'}
              </button>
            </div>

            <div className="space-y-2">
              <label className={`block ${labelClass}`}>Import from file, paste JSON, or graph export URL</label>
              <input
                type="file"
                accept=".json"
                className={`block w-full text-sm file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium ${
                  dark
                    ? 'text-slate-400 file:bg-green-900/40 file:text-green-300 hover:file:bg-green-800/40'
                    : 'text-gray-600 file:bg-green-50 file:text-green-700 hover:file:bg-green-100'
                }`}
                onChange={(e) => {
                  setImportFile(e.target.files?.[0] || null)
                  if (e.target.files?.[0]) {
                    setImportText('')
                    setImportUrl('')
                  }
                }}
              />
              <input
                type="url"
                placeholder="Or URL to JSON/YAML export (https://…)"
                value={importUrl}
                onChange={(e) => {
                  setImportUrl(e.target.value)
                  if (e.target.value.trim()) {
                    setImportFile(null)
                    setImportText('')
                  }
                }}
                className={`block w-full px-3 py-2 border rounded-lg text-sm ${
                  dark
                    ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-green-500'
                    : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-green-500'
                }`}
              />
              <textarea
                value={importText}
                onChange={(e) => {
                  setImportText(e.target.value)
                  if (e.target.value.trim()) {
                    setImportFile(null)
                    setImportUrl('')
                  }
                }}
                placeholder='{\n  "roots": [ ... ],\n  "nodes": [ ... ],\n  "edges": [ ... ]\n}'
                rows={4}
                className={`block w-full px-3 py-2 border rounded-lg text-sm font-mono resize-y ${
                  dark
                    ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-green-500 focus:border-green-500'
                    : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-green-500 focus:border-green-500'
                }`}
              />
              <div className="flex gap-3 items-center">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={purgeOnImport}
                    onChange={(e) => setPurgeOnImport(e.target.checked)}
                    className="rounded border-gray-300 dark:border-slate-600 text-green-600 focus:ring-green-500"
                  />
                  <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                    Purge existing
                  </span>
                </label>
                <button
                  onClick={handleImport}
                  disabled={
                    (!importFile && !importText.trim() && !importUrl.trim()) || importing
                  }
                  className="ml-auto px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 dark:bg-green-600 dark:hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {importing ? 'Importing...' : 'Import'}
                </button>
              </div>
            </div>

            {importExportError && (
              <p className="text-sm text-red-600 dark:text-red-400">{importExportError}</p>
            )}
          </div>
        )}

        {activeTab === 'chunks' && (
          <div className="space-y-4">
            <div className="flex flex-col lg:flex-row lg:flex-wrap gap-3 lg:items-end">
              <div className="flex-1 min-w-[200px]">
                <label className={`block ${labelClass} mb-1`}>Document</label>
                <select
                  value={chunksDocName}
                  onChange={(e) => setChunksDocName(e.target.value)}
                  className={inputClass}
                >
                  <option value="">Select a document</option>
                  {documents.map((d) => (
                    <option key={d.doc_name} value={d.doc_name}>
                      {d.doc_name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className={`block ${labelClass} mb-1`}>Filter chunks</label>
                <input
                  type="search"
                  value={chunkFilterInput}
                  onChange={(e) => setChunkFilterInput(e.target.value)}
                  placeholder="Search in title, text, summary…"
                  className={inputClass}
                />
              </div>
              <div className="w-full sm:w-auto">
                <label className={`block ${labelClass} mb-1`}>Per page</label>
                <select
                  value={chunksPerPage}
                  onChange={(e) => setChunksPerPage(Number(e.target.value))}
                  className={`${inputClass} min-w-[140px]`}
                >
                  {CHUNK_PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n === 0 ? 'All' : n}
                    </option>
                  ))}
                </select>
              </div>
              <div className="w-full sm:w-auto">
                <label className={`block ${labelClass} mb-1`}>RAG / chunks</label>
                <select
                  value={chunkEnabledFilter}
                  onChange={(e) =>
                    setChunkEnabledFilter(e.target.value as ChunkEnabledFilter)
                  }
                  className={`${inputClass} min-w-[180px]`}
                >
                  <option value="all">All chunks</option>
                  <option value="rag_enabled">RAG-enabled only</option>
                  <option value="rag_disabled">Disabled only</option>
                </select>
              </div>
            </div>

            <p className={`text-sm ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
              {chunksDocName ? (
                <>
                  Total chunks for <strong>{chunksDocName}</strong> (matching filter):{' '}
                  <strong>{chunksTotal}</strong>
                </>
              ) : (
                <>
                  Total chunks in collection (matching filter): <strong>{chunksTotal}</strong>
                </>
              )}
              {chunksPerPage === 0 && chunksTotal > 5000 && (
                <span className="text-amber-600 dark:text-amber-400 ml-2">
                  (list capped at 5000 per request)
                </span>
              )}
              {chunksPerPage > 0 && (
                <span className={`block sm:inline sm:ml-2 mt-1 sm:mt-0 ${dark ? 'text-slate-500' : 'text-gray-500'}`}>
                  Sorting applies to this page only; choose &quot;All&quot; or a larger page size to sort the full
                  filtered list.
                </span>
              )}
            </p>

            {chunksLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 dark:border-indigo-400" />
              </div>
            ) : (
              <>
                {chunksError ? (
                  <p className="text-sm text-red-600 dark:text-red-400 py-2" role="alert">
                    {chunksError}
                  </p>
                ) : null}
                {chunks.length === 0 && mergeQueue.length === 0 ? (
                  <p className={`text-sm py-4 ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                    No chunks match the current filter.
                  </p>
                ) : (
              <div className="space-y-3">
                {mergeQueue.length > 0 && (
                  <div
                    className={`rounded-lg border p-3 sm:p-4 space-y-3 ${
                      dark ? 'border-indigo-500/40 bg-slate-800/80' : 'border-indigo-200 bg-indigo-50/60'
                    }`}
                  >
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                      <div>
                        <h4
                          className={`text-sm font-semibold ${dark ? 'text-indigo-200' : 'text-indigo-900'}`}
                        >
                          Merge queue
                        </h4>
                        <p className={`text-xs mt-0.5 ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
                          First row is the kept chunk. Reorder with arrows, pick strategy, edit fields
                          if updating, choose actions, then apply.
                        </p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <label className={`text-xs ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
                          Strategy
                          <select
                            value={mergeStrategy}
                            onChange={(e) =>
                              setMergeStrategy(e.target.value as PageIndexChunkMergeStrategy)
                            }
                            disabled={mergingChunks}
                            className={`${inputClass} min-w-[160px] mt-0.5 text-xs py-1.5`}
                          >
                            <option value="concatenate">Concatenate titles &amp; text</option>
                            <option value="keep_first">Keep first title, append text</option>
                          </select>
                        </label>
                        <button
                          type="button"
                          onClick={handleMergeChunks}
                          disabled={
                            mergeQueue.length < 2 ||
                            mergingChunks ||
                            !mergeSameDocument ||
                            (!applyMergeUpdate && !applyMergeDeleteOthers) ||
                            (applyMergeUpdate && mergeDraft == null)
                          }
                          className="px-3 py-1.5 text-sm font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {mergingChunks ? 'Applying…' : 'Apply merge'}
                        </button>
                        <button
                          type="button"
                          onClick={clearMergeQueue}
                          disabled={mergingChunks}
                          className={`px-3 py-1.5 text-sm font-medium rounded-lg border ${
                            dark
                              ? 'border-slate-600 text-slate-200 hover:bg-slate-700'
                              : 'border-gray-300 text-gray-800 hover:bg-gray-100'
                          } disabled:opacity-50`}
                        >
                          Clear
                        </button>
                      </div>
                    </div>

                    <div
                      className={`space-y-2 text-xs ${dark ? 'text-slate-300' : 'text-gray-800'}`}
                    >
                      <label className="flex items-start gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={applyMergeUpdate}
                          onChange={(e) => setApplyMergeUpdate(e.target.checked)}
                          disabled={mergingChunks}
                          className="mt-0.5 rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                        />
                        <span>
                          Update kept chunk (first in list) with the merged fields below{' '}
                          <span className={dark ? 'text-slate-500' : 'text-gray-500'}>
                            (PATCH)
                          </span>
                        </span>
                      </label>
                      <label className="flex items-start gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={applyMergeDeleteOthers}
                          onChange={(e) => setApplyMergeDeleteOthers(e.target.checked)}
                          disabled={mergingChunks}
                          className="mt-0.5 rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                        />
                        <span>
                          Delete other chunks in this list{' '}
                          <span className={dark ? 'text-slate-500' : 'text-gray-500'}>
                            (no subtree). Sections with children may fail or leave an inconsistent
                            tree; use row <strong>Delete</strong> with subtree for parents when needed.
                          </span>
                        </span>
                      </label>
                    </div>

                    <ol
                      className={`list-decimal list-outside ml-5 space-y-2 text-sm ${dark ? 'text-slate-200' : 'text-gray-900'}`}
                    >
                      {mergeQueue.map((c, index) => (
                        <li key={c.id} className="pl-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="line-clamp-2 min-w-0 flex-1">
                              <span className="font-medium">{c.title || 'Untitled'}</span>
                              <span
                                className={`text-xs ml-2 ${dark ? 'text-slate-500' : 'text-gray-500'}`}
                              >
                                ({c.doc_name || '—'})
                              </span>
                            </span>
                            <div className="flex flex-wrap items-center gap-1 shrink-0">
                              <button
                                type="button"
                                onClick={() => moveMergeQueueItem(index, -1)}
                                disabled={mergingChunks || index === 0}
                                className="px-1.5 py-0.5 text-xs rounded border border-slate-500/50 dark:border-slate-600 disabled:opacity-40"
                                aria-label="Move up"
                              >
                                Up
                              </button>
                              <button
                                type="button"
                                onClick={() => moveMergeQueueItem(index, 1)}
                                disabled={mergingChunks || index === mergeQueue.length - 1}
                                className="px-1.5 py-0.5 text-xs rounded border border-slate-500/50 dark:border-slate-600 disabled:opacity-40"
                                aria-label="Move down"
                              >
                                Down
                              </button>
                              <button
                                type="button"
                                onClick={() => removeFromMergeQueue(c.id)}
                                disabled={mergingChunks}
                                className="text-xs text-red-600 dark:text-red-400 hover:underline disabled:opacity-50"
                              >
                                Remove
                              </button>
                            </div>
                          </div>
                        </li>
                      ))}
                    </ol>

                    {mergeQueue.length === 1 ? (
                      <p className={`text-xs ${dark ? 'text-slate-500' : 'text-gray-500'}`}>
                        Add another chunk to compute merged fields.
                      </p>
                    ) : null}

                    {mergeDraft && mergeQueue.length >= 2 ? (
                      <div
                        className={`rounded-md border p-3 space-y-3 ${
                          dark
                            ? 'border-slate-500/60 bg-slate-900/50'
                            : 'border-gray-300 bg-white/80'
                        }`}
                      >
                        <h5
                          className={`text-xs font-semibold uppercase tracking-wide ${
                            dark ? 'text-slate-300' : 'text-gray-700'
                          }`}
                        >
                          Merged fields (editable if updating)
                        </h5>
                        {!mergeSameDocument ? (
                          <p className="text-xs text-amber-700 dark:text-amber-300" role="status">
                            Chunks must belong to the same document before you can apply.
                          </p>
                        ) : null}
                        <p className={`text-xs ${dark ? 'text-slate-500' : 'text-gray-500'}`}>
                          Baseline refreshes when order or strategy changes. Clear fields to omit
                          summary or type when saving.
                        </p>
                        <div>
                          <label className={mergeFieldsLabelClass}>Title</label>
                          <input
                            type="text"
                            value={mergeDraft.title}
                            onChange={(e) =>
                              setMergeDraft((d) => (d ? { ...d, title: e.target.value } : d))
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            className={`${inputClass} mt-0.5 text-sm`}
                            aria-label="Merged title"
                          />
                        </div>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={mergeDraft.enabled}
                            onChange={(e) =>
                              setMergeDraft((d) =>
                                d ? { ...d, enabled: e.target.checked } : d
                              )
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            className="rounded border-gray-300 dark:border-slate-600 text-indigo-600"
                          />
                          <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-800'}`}>
                            Include chunk in RAG (enabled)
                          </span>
                        </label>
                        <div>
                          <label className={mergeFieldsLabelClass}>Content type</label>
                          <select
                            value={mergeDraft.contentType}
                            onChange={(e) =>
                              setMergeDraft((d) =>
                                d ? { ...d, contentType: e.target.value } : d
                              )
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            className={`w-full max-w-xs mt-0.5 text-sm rounded-md border py-1.5 pl-2 pr-1 ${
                              dark
                                ? 'border-slate-600 bg-slate-800 text-slate-100'
                                : 'border-gray-300 bg-white text-gray-900'
                            } disabled:opacity-50`}
                            aria-label="Merged content type"
                          >
                            {CHUNK_CONTENT_TYPE_OPTIONS.map((o) => (
                              <option key={o.value || '__empty'} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                            {mergeDraft.contentType &&
                              !CHUNK_CONTENT_TYPE_OPTIONS.some(
                                (o) => o.value === mergeDraft.contentType
                              ) && (
                                <option value={mergeDraft.contentType}>
                                  {mergeDraft.contentType}
                                </option>
                              )}
                          </select>
                        </div>
                        <div>
                          <label className={mergeFieldsLabelClass}>Summary</label>
                          <textarea
                            value={mergeDraft.summary}
                            onChange={(e) =>
                              setMergeDraft((d) =>
                                d ? { ...d, summary: e.target.value } : d
                              )
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            rows={4}
                            className={mergeTextareaClass}
                            aria-label="Merged summary"
                          />
                        </div>
                        <div>
                          <label className={mergeFieldsLabelClass}>Prefix summary</label>
                          <textarea
                            value={mergeDraft.prefixSummary}
                            onChange={(e) =>
                              setMergeDraft((d) =>
                                d ? { ...d, prefixSummary: e.target.value } : d
                              )
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            rows={3}
                            className={mergeTextareaClass}
                            aria-label="Merged prefix summary"
                          />
                        </div>
                        <div>
                          <label className={mergeFieldsLabelClass}>Text</label>
                          <textarea
                            value={mergeDraft.text}
                            onChange={(e) =>
                              setMergeDraft((d) => (d ? { ...d, text: e.target.value } : d))
                            }
                            disabled={mergingChunks || !applyMergeUpdate}
                            rows={6}
                            className={mergeTextareaClass}
                            aria-label="Merged text"
                          />
                        </div>
                      </div>
                    ) : null}
                  </div>
                )}

                {chunks.length === 0 ? (
                  <p className={`text-sm py-2 ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                    No further rows match the filter; adjust filters or clear the merge queue.
                  </p>
                ) : (
              <div
                className={`border rounded-lg overflow-hidden ${
                  dark ? 'border-slate-600' : 'border-gray-200'
                }`}
              >
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-600">
                    <thead className={dark ? 'bg-slate-800' : 'bg-gray-50'}>
                      <tr>
                        <th className={chunkThClass('max-w-[200px]')}>
                          <button
                            type="button"
                            className={chunkSortBtnClass}
                            onClick={() => toggleChunkSort('title')}
                          >
                            Title{chunkSortCaret('title')}
                          </button>
                        </th>
                        <th className={chunkThClass('min-w-[140px] max-w-[180px]')}>
                          <button
                            type="button"
                            className={chunkSortBtnClass}
                            onClick={() => toggleChunkSort('content_type')}
                          >
                            Type{chunkSortCaret('content_type')}
                          </button>
                        </th>
                        <th className={chunkThClass('whitespace-nowrap w-px')}>
                          <button
                            type="button"
                            className={chunkSortBtnClass}
                            onClick={() => toggleChunkSort('enabled')}
                          >
                            RAG{chunkSortCaret('enabled')}
                          </button>
                        </th>
                        <th className={chunkThClass('hidden md:table-cell max-w-md')}>Text</th>
                        <th className={chunkThClass('text-right')}>Actions</th>
                      </tr>
                    </thead>
                    <tbody className={`divide-y ${dark ? 'divide-slate-700 bg-slate-900' : 'divide-gray-200 bg-white'}`}>
                      {tableChunks.map((c) => {
                        const busy = quickSavingChunkId === c.id
                        const ctVal = c.content_type ?? ''
                        return (
                          <tr key={c.id} className={busy ? 'opacity-70' : undefined}>
                            <td className="px-3 py-2 text-sm max-w-[240px]">
                              <span className="line-clamp-2">{c.title || '—'}</span>
                              {!chunksDocName && c.doc_name ? (
                                <div
                                  className={`text-xs mt-0.5 truncate ${dark ? 'text-slate-500' : 'text-gray-500'}`}
                                  title={c.doc_name}
                                >
                                  {c.doc_name}
                                </div>
                              ) : null}
                            </td>
                            <td className="px-3 py-2 text-sm min-w-[140px] max-w-[200px]">
                              <select
                                value={ctVal}
                                onChange={(e) => {
                                  const v = e.target.value
                                  handleChunkQuickPatch(c, {
                                    content_type: v.trim() ? v.trim() : null,
                                  })
                                }}
                                disabled={busy}
                                className={`w-full max-w-[180px] text-xs rounded-md border py-1 pl-2 pr-1 ${
                                  dark
                                    ? 'border-slate-600 bg-slate-800 text-slate-100'
                                    : 'border-gray-300 bg-white text-gray-900'
                                } disabled:opacity-50`}
                              >
                                {CHUNK_CONTENT_TYPE_OPTIONS.map((o) => (
                                  <option key={o.value || '__empty'} value={o.value}>
                                    {o.label}
                                  </option>
                                ))}
                                {ctVal &&
                                  !CHUNK_CONTENT_TYPE_OPTIONS.some((o) => o.value === ctVal) && (
                                    <option value={ctVal}>{ctVal}</option>
                                  )}
                              </select>
                            </td>
                            <td className="px-3 py-2 text-sm whitespace-nowrap">
                              <label className="inline-flex items-center gap-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={c.enabled !== false}
                                  disabled={busy}
                                  onChange={(e) =>
                                    handleChunkQuickPatch(c, { enabled: e.target.checked })
                                  }
                                  className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500 disabled:opacity-50"
                                />
                                <span
                                  className={`text-xs font-medium ${
                                    c.enabled === false
                                      ? dark
                                        ? 'text-amber-200'
                                        : 'text-amber-900'
                                      : dark
                                        ? 'text-emerald-200'
                                        : 'text-emerald-900'
                                  }`}
                                >
                                  {c.enabled === false ? 'Off' : 'On'}
                                </span>
                              </label>
                            </td>
                            <td className="px-3 py-2 text-sm max-w-md hidden md:table-cell">
                              <span className={`line-clamp-3 ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                                {truncate(c.text || c.summary || '', 240)}
                              </span>
                            </td>
                            <td className="px-3 py-2 text-right">
                              <div className="flex flex-col items-end gap-1 sm:flex-row sm:flex-wrap sm:justify-end sm:gap-x-2 sm:gap-y-1">
                                <button
                                  type="button"
                                  onClick={() => addToMergeQueue(c)}
                                  disabled={
                                    busy ||
                                    mergingChunks ||
                                    mergeQueueIds.has(c.id) ||
                                    mergeQueue.length >= MERGE_QUEUE_MAX
                                  }
                                  className="text-amber-700 dark:text-amber-400 hover:underline text-sm font-medium disabled:opacity-50 whitespace-nowrap"
                                >
                                  Add to merge
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openEditChunk(c)}
                                  className="text-indigo-600 dark:text-indigo-400 hover:underline text-sm font-medium whitespace-nowrap"
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteChunk(c)}
                                  disabled={deletingChunkId === c.id || busy}
                                  className="text-red-600 dark:text-red-400 hover:underline text-sm font-medium disabled:opacity-50 whitespace-nowrap"
                                >
                                  {deletingChunkId === c.id ? 'Deleting…' : 'Delete'}
                                </button>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
                )}
              </div>
                )}
              </>
            )}

            {chunksPerPage > 0 && totalChunkPages > 1 && (
              <div className="flex items-center justify-between gap-3">
                <button
                  type="button"
                  disabled={chunksPage <= 1 || chunksLoading}
                  onClick={() => setChunksPage((p) => Math.max(1, p - 1))}
                  className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-slate-600 disabled:opacity-50"
                >
                  Previous
                </button>
                <span className={`text-sm ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
                  Page {chunksPage} of {totalChunkPages}
                </span>
                <button
                  type="button"
                  disabled={chunksPage >= totalChunkPages || chunksLoading}
                  onClick={() => setChunksPage((p) => p + 1)}
                  className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-slate-600 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'documents' && (
          <div className="space-y-6">
            <div className="space-y-3">
              <h3 className={`text-sm font-medium ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                Upload document
              </h3>
              <div className="flex flex-col sm:flex-row gap-3">
                <label className="flex-1 min-w-0">
                  <span className="sr-only">Choose file</span>
                  <input
                    type="file"
                    accept=".pdf,.md,.markdown,.txt,.docx,.doc,.xls,.xlsx,.ppt,.pptx"
                    className={`block w-full text-sm file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium ${
                      dark
                        ? 'text-slate-400 file:bg-indigo-900/40 file:text-indigo-300 hover:file:bg-indigo-800/40'
                        : 'text-gray-600 file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100'
                    }`}
                    onChange={(e) => {
                      const file = e.target.files?.[0] || null
                      setSelectedFile(file)
                      if (file) setIngestFileUrl('')
                      if (file && file.size > MAX_FILE_SIZE) {
                        setUploadError(`File exceeds ${MAX_FILE_SIZE / (1024 * 1024)}MB limit`)
                      } else {
                        setUploadError(null)
                      }
                    }}
                  />
                  {selectedFile && (
                    <p className={`mt-1 text-xs ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                      {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
                    </p>
                  )}
                </label>
                <input
                  type="text"
                  placeholder="Document name (optional)"
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  className={`flex-1 min-w-0 px-3 py-2 border rounded-lg text-sm ${
                    dark
                      ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-indigo-500'
                      : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-indigo-500'
                  }`}
                />
              </div>
              <div className="w-full">
                <label className={`block text-xs mb-1 ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                  Or paste a document URL (server downloads and ingests)
                </label>
                <input
                  type="url"
                  placeholder="https://…"
                  value={ingestFileUrl}
                  onChange={(e) => {
                    setIngestFileUrl(e.target.value)
                    if (e.target.value.trim()) {
                      setSelectedFile(null)
                      setUploadError(null)
                    }
                  }}
                  className={`block w-full px-3 py-2 border rounded-lg text-sm ${
                    dark
                      ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-indigo-500'
                      : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-indigo-500'
                  }`}
                />
              </div>
              <div className="w-full">
                <textarea
                  placeholder="Document description (optional)"
                  value={docDescription}
                  onChange={(e) => setDocDescription(e.target.value)}
                  rows={2}
                  className={`block w-full px-3 py-2 border rounded-lg text-sm resize-y min-h-[60px] ${
                    dark
                      ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-indigo-500'
                      : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-indigo-500'
                  }`}
                />
              </div>
              <div className="w-full">
                <input
                  type="url"
                  placeholder="Source URL (optional, for reference citations)"
                  value={docUrl}
                  onChange={(e) => setDocUrl(e.target.value)}
                  className={`block w-full px-3 py-2 border rounded-lg text-sm ${
                    dark
                      ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-indigo-500'
                      : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-indigo-500'
                  }`}
                />
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={addNodeSummary}
                  onChange={(e) => setAddNodeSummary(e.target.checked)}
                  className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
                />
                <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                  Generate node summaries (recommended for tree search)
                </span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={convertToMarkdown}
                  onChange={(e) => setConvertToMarkdown(e.target.checked)}
                  className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
                />
                <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                  Convert PDF with Docling to Markdown first (requires server jvagent[pageindex])
                </span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={doclingOcr}
                  onChange={(e) => setDoclingOcr(e.target.checked)}
                  disabled={!convertToMarkdown}
                  className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500 disabled:opacity-50"
                />
                <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                  Enable OCR in Docling (scanned PDFs)
                </span>
              </label>
              <div className="w-full">
                <input
                  type="text"
                  placeholder='Metadata (optional JSON, e.g. {"doc_name":"","doc_url":"","access":"public"})'
                  value={metadataJson}
                  onChange={(e) => setMetadataJson(e.target.value)}
                  className={`block w-full px-3 py-2 border rounded-lg text-sm ${
                    dark
                      ? 'border-slate-600 bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-indigo-500'
                      : 'border-gray-300 bg-white text-gray-900 placeholder-gray-500 focus:ring-indigo-500'
                  }`}
                />
                {metadataJson.trim() && !parseMetadata() && (
                  <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                    Invalid JSON – metadata will be ignored
                  </p>
                )}
              </div>
              <div className="flex justify-end">
                <button
                  onClick={handleUpload}
                  disabled={(!selectedFile && !ingestFileUrl.trim()) || uploading}
                  className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0"
                >
                  {uploading ? 'Uploading...' : 'Upload'}
                </button>
              </div>
              {uploadError && (
                <p className="text-sm text-red-600 dark:text-red-400">{uploadError}</p>
              )}
            </div>

            <div className="space-y-3">
              <h3 className={`text-sm font-medium ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                Indexed documents
              </h3>
              {loading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 dark:border-indigo-400" />
                </div>
              ) : error ? (
                <p className="text-sm text-red-600 dark:text-red-400 py-4">{error}</p>
              ) : documents.length === 0 ? (
                <p className={`text-sm py-4 ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                  No documents indexed yet.
                </p>
              ) : (
                <div
                  className={`border rounded-lg overflow-hidden ${
                    dark ? 'border-slate-600' : 'border-gray-200'
                  }`}
                >
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-600">
                      <thead className={dark ? 'bg-slate-800' : 'bg-gray-50'}>
                        <tr>
                          <th
                            className={`px-4 py-2 text-left text-xs font-medium uppercase ${
                              dark ? 'text-slate-400' : 'text-gray-500'
                            }`}
                          >
                            Name
                          </th>
                          <th
                            className={`px-4 py-2 text-left text-xs font-medium uppercase hidden sm:table-cell ${
                              dark ? 'text-slate-400' : 'text-gray-500'
                            }`}
                          >
                            Description
                          </th>
                          <th
                            className={`px-4 py-2 text-left text-xs font-medium uppercase hidden md:table-cell ${
                              dark ? 'text-slate-400' : 'text-gray-500'
                            }`}
                          >
                            Source URL
                          </th>
                          <th
                            className={`px-4 py-2 text-left text-xs font-medium uppercase hidden lg:table-cell ${
                              dark ? 'text-slate-400' : 'text-gray-500'
                            }`}
                          >
                            Metadata
                          </th>
                          <th
                            className={`px-4 py-2 text-right text-xs font-medium uppercase ${
                              dark ? 'text-slate-400' : 'text-gray-500'
                            }`}
                          >
                            Actions
                          </th>
                        </tr>
                      </thead>
                      <tbody
                        className={`divide-y ${dark ? 'divide-slate-700 bg-slate-900' : 'divide-gray-200 bg-white'}`}
                      >
                        {documents.map((doc) => (
                          <tr key={doc.doc_name}>
                            <td className={`px-4 py-3 text-sm ${dark ? 'text-slate-100' : 'text-gray-900'}`}>
                              {doc.doc_name}
                            </td>
                            <td
                              className={`px-4 py-3 text-sm hidden sm:table-cell max-w-[200px] truncate ${
                                dark ? 'text-slate-300' : 'text-gray-600'
                              }`}
                            >
                              {doc.doc_description || '—'}
                            </td>
                            <td className="px-4 py-3 text-sm hidden md:table-cell max-w-[180px]">
                              {doc.doc_url ? (
                                <a
                                  href={doc.doc_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-indigo-600 dark:text-indigo-400 hover:underline truncate block max-w-[180px]"
                                  title={doc.doc_url}
                                >
                                  {doc.doc_url}
                                </a>
                              ) : (
                                <span className={dark ? 'text-slate-400' : 'text-gray-500'}>—</span>
                              )}
                            </td>
                            <td
                              className={`px-4 py-3 text-sm hidden lg:table-cell max-w-[150px] truncate ${
                                dark ? 'text-slate-400' : 'text-gray-500'
                              }`}
                              title={doc.metadata ? JSON.stringify(doc.metadata) : undefined}
                            >
                              {doc.metadata ? JSON.stringify(doc.metadata) : '—'}
                            </td>
                            <td className="px-4 py-3 text-right">
                              <button
                                onClick={() => handleDelete(doc.doc_name)}
                                disabled={deleting === doc.doc_name}
                                className="text-red-600 hover:text-red-800 dark:text-red-400 dark:hover:text-red-300 disabled:opacity-50 text-sm font-medium"
                              >
                                {deleting === doc.doc_name ? 'Deleting...' : 'Delete'}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {editingChunk && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 dark:bg-black/70"
          onClick={closeEditChunk}
        >
          <div
            className={`w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-lg shadow-xl border p-4 sm:p-6 ${
              dark ? 'bg-slate-900 border-slate-600' : 'bg-white border-gray-200'
            }`}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className={`text-lg font-semibold mb-3 ${dark ? 'text-slate-100' : 'text-gray-900'}`}>
              Edit chunk
            </h3>
            <div className={`space-y-2 mb-4 text-xs ${dark ? 'text-slate-400' : 'text-gray-600'}`}>
              <div>
                <span className="font-medium text-slate-500 dark:text-slate-500">Graph node id</span>
                <p className="font-mono break-all mt-0.5">{editingChunk.id}</p>
              </div>
              <div>
                <span className="font-medium">node_id</span>
                <p className="font-mono mt-0.5">{editingChunk.node_id || '—'}</p>
              </div>
              <div>
                <span className="font-medium">doc_name</span>
                <p className="mt-0.5">{editingChunk.doc_name || '—'}</p>
              </div>
              <div>
                <span className="font-medium">collection_name</span>
                <p className="font-mono mt-0.5 break-all">
                  {documents.find((d) => d.doc_name === editingChunk.doc_name)?.collection_name ??
                    agentId}
                </p>
              </div>
              {editingChunk.hierarchy && editingChunk.hierarchy.length > 0 && (
                <div>
                  <span className="font-medium">hierarchy</span>
                  <p className="mt-0.5 text-xs break-words">
                    {editingChunk.hierarchy.join(' › ')}
                  </p>
                </div>
              )}
            </div>
            <div className="space-y-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editEnabled}
                  onChange={(e) => setEditEnabled(e.target.checked)}
                  className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
                />
                <span className={`text-sm ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                  Include chunk in RAG (enabled)
                </span>
              </label>
              <div>
                <label className={`block ${labelClass} mb-1`}>Content type</label>
                <input
                  type="text"
                  className={inputClass}
                  list="pageindex-content-type-suggestions"
                  value={editContentType}
                  onChange={(e) => setEditContentType(e.target.value)}
                  placeholder="substantive, heading_like, appendix, … (empty to clear)"
                />
                <datalist id="pageindex-content-type-suggestions">
                  {CHUNK_CONTENT_TYPE_OPTIONS.filter((o) => o.value).map((o) => (
                    <option key={o.value} value={o.value} />
                  ))}
                </datalist>
              </div>
              <div>
                <label className={`block ${labelClass} mb-1`}>Title</label>
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className={`block ${labelClass} mb-1`}>Summary</label>
                <textarea
                  value={editSummary}
                  onChange={(e) => setEditSummary(e.target.value)}
                  rows={3}
                  className={`${inputClass} resize-y`}
                />
              </div>
              <div>
                <label className={`block ${labelClass} mb-1`}>Text</label>
                <textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  rows={8}
                  className={`${inputClass} resize-y font-mono text-xs`}
                />
              </div>
              <div>
                <label className={`block ${labelClass} mb-1`}>Metadata (document root)</label>
                <p className={`text-xs mb-1 ${dark ? 'text-amber-400/90' : 'text-amber-800'}`}>
                  If you change this metadata, you update the document root node; it applies to all chunks in
                  this document.
                </p>
                <textarea
                  value={editRootMetadataJson}
                  onChange={(e) => setEditRootMetadataJson(e.target.value)}
                  rows={6}
                  spellCheck={false}
                  className={`w-full pl-8 pr-3 py-2 border rounded-lg text-xs font-mono leading-relaxed resize-y whitespace-pre ${
                    dark
                      ? 'border-slate-600 bg-slate-950 text-slate-100'
                      : 'border-gray-300 bg-gray-50 text-gray-900'
                  } focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500`}
                />
              </div>
            </div>
            {saveChunkError && (
              <p className="text-sm text-red-600 dark:text-red-400 mt-3">{saveChunkError}</p>
            )}
            <div className="flex justify-end gap-2 mt-6">
              <button
                type="button"
                onClick={closeEditChunk}
                className={`px-4 py-2 text-sm rounded-lg border ${
                  dark ? 'border-slate-600 text-slate-200 hover:bg-slate-800' : 'border-gray-300 text-gray-700 hover:bg-gray-50'
                }`}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveChunk}
                disabled={savingChunk}
                className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {savingChunk ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )

  if (isEmbedded) {
    return (
      <div
        className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${dark ? 'bg-black/70' : 'bg-black/50'}`}
        onClick={(e) => {
          if (e.target === e.currentTarget && onClose) onClose()
        }}
      >
        {content}
      </div>
    )
  }

  return content
}
