import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../config/api'
import type { PageIndexDocument } from '../types/api'

interface PageIndexDocumentsModalProps {
  agentId: string
  onClose: () => void
  isEmbedded?: boolean
}

export function PageIndexDocumentsModal({
  agentId,
  onClose,
  isEmbedded = true,
}: PageIndexDocumentsModalProps) {
  const [documents, setDocuments] = useState<PageIndexDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [docName, setDocName] = useState('')
  const [docDescription, setDocDescription] = useState('')
  const [metadataJson, setMetadataJson] = useState('')
  const [addNodeSummary, setAddNodeSummary] = useState(true)
  const [purgeOnImport, setPurgeOnImport] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportCollectionName, setExportCollectionName] = useState('default')
  const [importExportError, setImportExportError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'import-export' | 'documents'>('documents')
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
    // Use application/octet-stream so multipart parser treats as binary (avoids UTF-8 decode errors)
    return new File([clean], file.name, { type: 'application/octet-stream' })
  }

  const handleUpload = async () => {
    if (!selectedFile) return

    // Validate file size
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
        metadata: parseMetadata(),
        ifAddNodeSummary: addNodeSummary,
      })
      setSelectedFile(null)
      setDocName('')
      setDocDescription('')
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 dark:bg-black/70"
      onClick={(e) => {
        if (e.target === e.currentTarget && onClose) onClose()
      }}
    >
      <div
        className="bg-white dark:bg-slate-900 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col border border-gray-200 dark:border-slate-700"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex-shrink-0 border-b border-gray-200 dark:border-slate-700 px-4 sm:px-6 py-4 flex items-center justify-between">
          <h2 className="text-xl sm:text-2xl font-semibold text-gray-900 dark:text-slate-100">
            Documents
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            aria-label="Close"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Tabs */}
        <div className="flex-shrink-0 border-b border-gray-200 dark:border-slate-700">
          <nav className="flex gap-1 px-4 sm:px-6" aria-label="Tabs">
            <button
              type="button"
              onClick={() => setActiveTab('documents')}
              className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
                activeTab === 'documents'
                  ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-slate-400 hover:text-gray-700 dark:hover:text-slate-300 hover:border-gray-300 dark:hover:border-slate-600'
              }`}
            >
              Upload & List
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('import-export')}
              className={`py-3 px-4 text-sm font-medium border-b-2 transition-colors -mb-px ${
                activeTab === 'import-export'
                  ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-slate-400 hover:text-gray-700 dark:hover:text-slate-300 hover:border-gray-300 dark:hover:border-slate-600'
              }`}
            >
              Import / Export
            </button>
          </nav>
        </div>

        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
          {activeTab === 'import-export' && (
            <div className="space-y-6">
              {/* Export */}
            <div className="flex flex-col sm:flex-row gap-3 items-end">
              <div className="flex-1">
                <label className="block text-xs text-gray-600 dark:text-slate-400 mb-1">Collection name</label>
                <input
                  type="text"
                  value={exportCollectionName}
                  onChange={(e) => setExportCollectionName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg text-sm bg-white dark:bg-slate-800 text-gray-900 dark:text-slate-100 focus:ring-2 focus:ring-green-500 focus:border-green-500 dark:focus:ring-green-500 dark:focus:border-green-500"
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

            {/* Import */}
            <div className="space-y-2">
              <label className="block text-xs text-gray-600 dark:text-slate-400">Import from file or paste JSON</label>
              <input
                type="file"
                accept=".json"
                className="block w-full text-sm text-gray-600 dark:text-slate-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-green-50 file:text-green-700 hover:file:bg-green-100 dark:file:bg-green-900/40 dark:file:text-green-300 dark:hover:file:bg-green-800/40"
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
                className="block w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg text-sm font-mono bg-white dark:bg-slate-800 text-gray-900 dark:text-slate-100 placeholder-gray-500 dark:placeholder-slate-400 focus:ring-2 focus:ring-green-500 focus:border-green-500 dark:focus:ring-green-500 dark:focus:border-green-500 resize-y"
              />
              <div className="flex gap-3 items-center">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={purgeOnImport}
                    onChange={(e) => setPurgeOnImport(e.target.checked)}
                    className="rounded border-gray-300 dark:border-slate-600 text-green-600 focus:ring-green-500"
                  />
                  <span className="text-sm text-gray-700 dark:text-slate-300">Purge existing</span>
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

          {activeTab === 'documents' && (
            <div className="space-y-6">
          {/* Upload section */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-gray-700 dark:text-slate-300">Upload document</h3>
            <div className="flex flex-col sm:flex-row gap-3">
              <label className="flex-1 min-w-0">
                <span className="sr-only">Choose file</span>
                <input
                  type="file"
                  accept=".pdf,.md,.markdown"
                  className="block w-full text-sm text-gray-600 dark:text-slate-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100 dark:file:bg-indigo-900/40 dark:file:text-indigo-300 dark:hover:file:bg-indigo-800/40"
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
                  <p className="mt-1 text-xs text-gray-500 dark:text-slate-400">
                    {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
                  </p>
                )}
              </label>
              <input
                type="text"
                placeholder="Document name (optional)"
                value={docName}
                onChange={(e) => setDocName(e.target.value)}
                className="flex-1 min-w-0 px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg text-sm bg-white dark:bg-slate-800 text-gray-900 dark:text-slate-100 placeholder-gray-500 dark:placeholder-slate-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 dark:focus:ring-indigo-500 dark:focus:border-indigo-500"
              />
            </div>
            <div className="w-full">
              <textarea
                placeholder="Document description (optional)"
                value={docDescription}
                onChange={(e) => setDocDescription(e.target.value)}
                rows={2}
                className="block w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg text-sm bg-white dark:bg-slate-800 text-gray-900 dark:text-slate-100 placeholder-gray-500 dark:placeholder-slate-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 dark:focus:ring-indigo-500 dark:focus:border-indigo-500 resize-y min-h-[60px]"
              />
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={addNodeSummary}
                onChange={(e) => setAddNodeSummary(e.target.checked)}
                className="rounded border-gray-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700 dark:text-slate-300">Generate node summaries (recommended for tree search)</span>
            </label>
            <div className="w-full">
              <input
                type="text"
                placeholder='Metadata (optional JSON, e.g. {"topic": "finance", "year": 2024})'
                value={metadataJson}
                onChange={(e) => setMetadataJson(e.target.value)}
                className="block w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg text-sm bg-white dark:bg-slate-800 text-gray-900 dark:text-slate-100 placeholder-gray-500 dark:placeholder-slate-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 dark:focus:ring-indigo-500 dark:focus:border-indigo-500"
              />
              {metadataJson.trim() && !parseMetadata() && (
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">Invalid JSON – metadata will be ignored</p>
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

          {/* Document list */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-gray-700 dark:text-slate-300">Indexed documents</h3>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 dark:border-indigo-400" />
              </div>
            ) : error ? (
              <p className="text-sm text-red-600 dark:text-red-400 py-4">{error}</p>
            ) : documents.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-slate-400 py-4">No documents indexed yet.</p>
            ) : (
              <div className="border border-gray-200 dark:border-slate-600 rounded-lg overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-600">
                    <thead className="bg-gray-50 dark:bg-slate-800">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-slate-400 uppercase">
                          Name
                        </th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-slate-400 uppercase hidden sm:table-cell">
                          Description
                        </th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-slate-400 uppercase hidden md:table-cell">
                          Metadata
                        </th>
                        <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-slate-400 uppercase">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody className="bg-white dark:bg-slate-900 divide-y divide-gray-200 dark:divide-slate-700">
                      {documents.map((doc) => (
                        <tr key={doc.doc_name}>
                          <td className="px-4 py-3 text-sm text-gray-900 dark:text-slate-100">
                            {doc.doc_name}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-600 dark:text-slate-300 hidden sm:table-cell max-w-[200px] truncate">
                            {doc.doc_description || '—'}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500 dark:text-slate-400 hidden md:table-cell max-w-[150px] truncate" title={doc.metadata ? JSON.stringify(doc.metadata) : undefined}>
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
      </div>
    </div>
  )
}
