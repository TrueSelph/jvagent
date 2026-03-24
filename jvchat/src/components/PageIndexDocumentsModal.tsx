import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../config/api'
import type { PageIndexChunk, PageIndexDocument } from '../types/api'
import { useTheme } from '../context/ThemeContext'

interface PageIndexDocumentsModalProps {
  agentId: string
  onClose: () => void
  isEmbedded?: boolean
}

const CHUNK_PAGE_SIZES = [0, 10, 25, 50, 100] as const

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
  const [docName, setDocName] = useState('')
  const [docDescription, setDocDescription] = useState('')
  const [docUrl, setDocUrl] = useState('')
  const [metadataJson, setMetadataJson] = useState('')
  const [addNodeSummary, setAddNodeSummary] = useState(true)
  const [purgeOnImport, setPurgeOnImport] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportCollectionName, setExportCollectionName] = useState('default')
  const [importExportError, setImportExportError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'import-export' | 'documents' | 'chunks'>(
    'documents'
  )

  const [chunksDocName, setChunksDocName] = useState('')
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
  }, [chunksDocName, chunkFilterQ, chunksPerPage])

  const fetchChunks = useCallback(async () => {
    if (activeTab !== 'chunks') return
    setChunksLoading(true)
    setChunksError(null)
    try {
      const params = {
        page: chunksPerPage === 0 ? 1 : chunksPage,
        per_page: chunksPerPage,
        q: chunkFilterQ.trim() || undefined,
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
  }, [agentId, chunksDocName, activeTab, chunksPage, chunksPerPage, chunkFilterQ])

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
    if (!selectedFile) return

    if (selectedFile.size > MAX_FILE_SIZE) {
      setUploadError(`File size exceeds ${MAX_FILE_SIZE / (1024 * 1024)}MB limit`)
      return
    }

    setUploading(true)
    setUploadError(null)
    try {
      const fileToUpload = await normalizeMarkdownForUpload(selectedFile)
      await apiClient.uploadPageIndexDocument(agentId, fileToUpload, {
        docName: docName || undefined,
        docDescription: docDescription || undefined,
        docUrl: docUrl || undefined,
        metadata: parseMetadata(),
        ifAddNodeSummary: addNodeSummary,
      })
      setSelectedFile(null)
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
      const data = await apiClient.exportPageIndex('json', exportCollectionName)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `pageindex-${exportCollectionName}-${Date.now()}.json`
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
    const source = importText.trim() || (importFile ? await importFile.text() : '')
    if (!source) return
    setImporting(true)
    setImportExportError(null)
    try {
      const data = JSON.parse(source)
      await apiClient.importPageIndex(agentId, data, purgeOnImport)
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

  const inputClass = dark
    ? 'w-full px-3 py-2 border border-slate-600 rounded-lg text-sm bg-slate-800 text-slate-100 placeholder-slate-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500'
    : 'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white text-gray-900 placeholder-gray-500 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500'

  const labelClass = dark ? 'text-xs text-slate-400' : 'text-xs text-gray-600'

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
        </nav>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-4">
        {activeTab === 'import-export' && (
          <div className="space-y-6">
            <div className="flex flex-col sm:flex-row gap-3 items-end">
              <div className="flex-1">
                <label className={`block ${labelClass} mb-1`}>Collection name</label>
                <input
                  type="text"
                  value={exportCollectionName}
                  onChange={(e) => setExportCollectionName(e.target.value)}
                  className={inputClass}
                />
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
              <label className={`block ${labelClass}`}>Import from file or paste JSON</label>
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
                  if (e.target.files?.[0]) setImportText('')
                }}
              />
              <textarea
                value={importText}
                onChange={(e) => {
                  setImportText(e.target.value)
                  if (e.target.value.trim()) setImportFile(null)
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
                  disabled={(!importFile && !importText.trim()) || importing}
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
            </p>

            {chunksLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 dark:border-indigo-400" />
              </div>
            ) : chunksError ? (
              <p className="text-sm text-red-600 dark:text-red-400 py-4">{chunksError}</p>
            ) : chunks.length === 0 ? (
              <p className={`text-sm py-4 ${dark ? 'text-slate-400' : 'text-gray-500'}`}>
                No chunks match the current filter.
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
                          className={`px-3 py-2 text-left text-xs font-medium uppercase ${
                            dark ? 'text-slate-400' : 'text-gray-500'
                          }`}
                        >
                          Title
                        </th>
                        <th
                          className={`px-3 py-2 text-left text-xs font-medium uppercase hidden md:table-cell ${
                            dark ? 'text-slate-400' : 'text-gray-500'
                          }`}
                        >
                          Text
                        </th>
                        <th
                          className={`px-3 py-2 text-right text-xs font-medium uppercase ${
                            dark ? 'text-slate-400' : 'text-gray-500'
                          }`}
                        >
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody className={`divide-y ${dark ? 'divide-slate-700 bg-slate-900' : 'divide-gray-200 bg-white'}`}>
                      {chunks.map((c) => (
                        <tr key={c.id}>
                          <td className="px-3 py-2 text-sm max-w-[240px]">
                            {!chunksDocName && (
                              <div
                                className={`text-xs mb-0.5 truncate ${
                                  dark ? 'text-slate-500' : 'text-gray-500'
                                }`}
                                title={c.doc_name}
                              >
                                {c.doc_name || '—'}
                              </div>
                            )}
                            <span className="line-clamp-2">{c.title || '—'}</span>
                          </td>
                          <td className="px-3 py-2 text-sm max-w-md hidden md:table-cell">
                            <span className={`line-clamp-3 ${dark ? 'text-slate-300' : 'text-gray-700'}`}>
                              {truncate(c.text || c.summary || '', 240)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right whitespace-nowrap">
                            <button
                              type="button"
                              onClick={() => openEditChunk(c)}
                              className="text-indigo-600 dark:text-indigo-400 hover:underline text-sm font-medium mr-3"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeleteChunk(c)}
                              disabled={deletingChunkId === c.id}
                              className="text-red-600 dark:text-red-400 hover:underline text-sm font-medium disabled:opacity-50"
                            >
                              {deletingChunkId === c.id ? 'Deleting…' : 'Delete'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
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
                    accept=".pdf,.md,.markdown"
                    className={`block w-full text-sm file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium ${
                      dark
                        ? 'text-slate-400 file:bg-indigo-900/40 file:text-indigo-300 hover:file:bg-indigo-800/40'
                        : 'text-gray-600 file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100'
                    }`}
                    onChange={(e) => {
                      const file = e.target.files?.[0] || null
                      setSelectedFile(file)
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
              <div className="w-full">
                <input
                  type="text"
                  placeholder='Metadata (optional JSON, e.g. {"topic": "finance", "year": 2024})'
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
                  disabled={!selectedFile || uploading}
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
            </div>
            <div className="space-y-3">
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
