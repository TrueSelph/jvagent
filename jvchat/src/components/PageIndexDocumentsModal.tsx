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
      setUploadError(err.message || 'Upload failed')
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black bg-opacity-50"
      onClick={(e) => {
        if (e.target === e.currentTarget && onClose) onClose()
      }}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex-shrink-0 border-b border-gray-200 px-4 sm:px-6 py-4 flex items-center justify-between">
          <h2 className="text-xl sm:text-2xl font-semibold text-gray-900">
            Document Index
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
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

        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 space-y-6">
          {/* Upload section */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-gray-700">Upload document</h3>
            <div className="flex flex-col sm:flex-row gap-3">
              <label className="flex-1 min-w-0">
                <span className="sr-only">Choose file</span>
                <input
                  type="file"
                  accept=".pdf,.md,.markdown"
                  className="block w-full text-sm text-gray-600 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
                  onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                />
              </label>
              <input
                type="text"
                placeholder="Document name (optional)"
                value={docName}
                onChange={(e) => setDocName(e.target.value)}
                className="flex-1 min-w-0 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              />
            </div>
            <div className="w-full">
              <textarea
                placeholder="Document description (optional)"
                value={docDescription}
                onChange={(e) => setDocDescription(e.target.value)}
                rows={2}
                className="block w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 resize-y min-h-[60px]"
              />
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={addNodeSummary}
                onChange={(e) => setAddNodeSummary(e.target.checked)}
                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700">Generate node summaries (recommended for tree search)</span>
            </label>
            <div className="w-full">
              <input
                type="text"
                placeholder='Metadata (optional JSON, e.g. {"topic": "finance", "year": 2024})'
                value={metadataJson}
                onChange={(e) => setMetadataJson(e.target.value)}
                className="block w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              />
              {metadataJson.trim() && !parseMetadata() && (
                <p className="mt-1 text-xs text-amber-600">Invalid JSON – metadata will be ignored</p>
              )}
            </div>
            <div className="flex justify-end">
              <button
                onClick={handleUpload}
                disabled={!selectedFile || uploading}
                className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0"
              >
                {uploading ? 'Uploading...' : 'Upload'}
              </button>
            </div>
            {uploadError && (
              <p className="text-sm text-red-600">{uploadError}</p>
            )}
          </div>

          {/* Document list */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-gray-700">Indexed documents</h3>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" />
              </div>
            ) : error ? (
              <p className="text-sm text-red-600 py-4">{error}</p>
            ) : documents.length === 0 ? (
              <p className="text-sm text-gray-500 py-4">No documents indexed yet.</p>
            ) : (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Name
                        </th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase hidden sm:table-cell">
                          Description
                        </th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase hidden md:table-cell">
                          Metadata
                        </th>
                        <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {documents.map((doc) => (
                        <tr key={doc.doc_name}>
                          <td className="px-4 py-3 text-sm text-gray-900">
                            {doc.doc_name}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-600 hidden sm:table-cell max-w-[200px] truncate">
                            {doc.doc_description || '—'}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500 hidden md:table-cell max-w-[150px] truncate" title={doc.metadata ? JSON.stringify(doc.metadata) : undefined}>
                            {doc.metadata ? JSON.stringify(doc.metadata) : '—'}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <button
                              onClick={() => handleDelete(doc.doc_name)}
                              disabled={deleting === doc.doc_name}
                              className="text-red-600 hover:text-red-800 disabled:opacity-50 text-sm font-medium"
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
      </div>
    </div>
  )
}
