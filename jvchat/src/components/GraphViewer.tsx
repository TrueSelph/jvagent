import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import mermaid from 'mermaid'
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch'
import { apiClient } from '../config/api'

interface GraphViewerProps {
  onClose?: () => void
  isEmbedded?: boolean
}

export function GraphViewer({ onClose, isEmbedded = false }: GraphViewerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [graphData, setGraphData] = useState<string | null>(null)
  const mermaidRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    // Initialize mermaid
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
      flowchart: {
        useMaxWidth: true,
        htmlLabels: true,
        curve: 'basis',
      },
    })

    return () => {
      // Cleanup on unmount
      if (mermaidRef.current) {
        mermaidRef.current.innerHTML = ''
      }
    }
  }, [])

  const fetchGraph = async () => {
    setLoading(true)
    setError(null)

    try {
      const data = await apiClient.getGraph('mermaid', true)
      setGraphData(data)
    } catch (err: any) {
      console.error('Failed to fetch graph:', err)
      setError(err.message || 'Failed to load graph data')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchGraph()
  }, [])

  useEffect(() => {
    if (graphData && mermaidRef.current && !loading) {
      // Clear previous render
      mermaidRef.current.innerHTML = ''

      // Generate unique ID for this render
      const renderId = `mermaid-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

      // Render mermaid diagram
      mermaid
        .render(renderId, graphData)
        .then((result) => {
          if (mermaidRef.current) {
            mermaidRef.current.innerHTML = result.svg
          }
        })
        .catch((err) => {
          console.error('Mermaid rendering error:', err)
          setError('Failed to render graph diagram')
        })
    }
  }, [graphData, loading])

  const handleRefresh = () => {
    fetchGraph()
  }

  const handleClose = () => {
    if (onClose) {
      onClose()
    } else {
      navigate('/agents')
    }
  }

  // Handle escape key
  useEffect(() => {
    if (!isEmbedded || !onClose) return

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }

    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [isEmbedded, onClose])

  if (isEmbedded) {
    // Embedded mode (modal dialog)
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black bg-opacity-50"
        onClick={(e) => {
          // Close on backdrop click
          if (e.target === e.currentTarget && onClose) {
            onClose()
          }
        }}
      >
        <div
          className="bg-white rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex-shrink-0 border-b border-gray-200 px-4 sm:px-6 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-xl sm:text-2xl font-semibold text-gray-900">App Graph</h2>
              <span className="hidden sm:inline text-xs text-gray-500">
                (Drag to pan, scroll to zoom, double-click to zoom in)
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRefresh}
                disabled={loading}
                className="px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                title="Refresh graph"
              >
                <svg
                  className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
                <span className="hidden sm:inline">Refresh</span>
              </button>
              {onClose && (
                <button
                  onClick={handleClose}
                  className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
                  title="Close"
                  aria-label="Close graph viewer"
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
              )}
            </div>
          </div>

          {/* Content with pan/zoom */}
          <div className="flex-1 overflow-hidden relative">
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center bg-white">
                <div className="text-center">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
                  <p className="mt-4 text-gray-600">Loading graph...</p>
                </div>
              </div>
            )}

            {error && (
              <div className="absolute inset-0 flex items-center justify-center bg-white p-4">
                <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md w-full">
                  <h3 className="text-lg font-semibold text-red-800 mb-2">Error Loading Graph</h3>
                  <p className="text-red-700 mb-4">{error}</p>
                  <button
                    onClick={handleRefresh}
                    className="w-full px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}

            {!loading && !error && (
              <TransformWrapper
                initialScale={1}
                minScale={0.1}
                maxScale={4}
                limitToBounds={false}
                centerOnInit={true}
                wheel={{
                  step: 0.1,
                }}
                doubleClick={{
                  disabled: false,
                  step: 0.7,
                }}
              >
                {({ zoomIn, zoomOut, resetTransform }) => (
                  <>
                    {/* Zoom Controls */}
                    <div className="absolute top-4 right-4 z-10 flex flex-col gap-2 bg-white rounded-lg shadow-lg border border-gray-200 p-2">
                      <button
                        onClick={() => zoomIn()}
                        className="p-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                        title="Zoom in"
                        aria-label="Zoom in"
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
                            d="M12 4v16m8-8H4"
                          />
                        </svg>
                      </button>
                      <button
                        onClick={() => zoomOut()}
                        className="p-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                        title="Zoom out"
                        aria-label="Zoom out"
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
                            d="M20 12H4"
                          />
                        </svg>
                      </button>
                      <button
                        onClick={() => resetTransform()}
                        className="p-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                        title="Reset zoom"
                        aria-label="Reset zoom"
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
                            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                          />
                        </svg>
                      </button>
                    </div>

                    {/* Graph Content */}
                    <TransformComponent
                      wrapperClass="w-full h-full"
                      contentClass="flex items-center justify-center"
                    >
                      <div
                        ref={mermaidRef}
                        className="flex justify-center items-center"
                        style={{ minWidth: '800px', minHeight: '600px' }}
                      />
                    </TransformComponent>
                  </>
                )}
              </TransformWrapper>
            )}
          </div>
        </div>
      </div>
    )
  }

  // Standalone page mode
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="bg-white border-b border-gray-200 px-4 sm:px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/agents')}
              className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              title="Back to agents"
              aria-label="Back to agents"
            >
              <svg
                className="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 19l-7-7m0 0l7-7m-7 7h18"
                />
              </svg>
            </button>
            <h1 className="text-xl sm:text-2xl font-bold text-gray-900">App Graph</h1>
          </div>
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            <svg
              className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        {loading && (
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading graph...</p>
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md w-full">
              <h3 className="text-lg font-semibold text-red-800 mb-2">Error Loading Graph</h3>
              <p className="text-red-700 mb-4">{error}</p>
              <button
                onClick={handleRefresh}
                className="w-full px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {!loading && !error && (
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 sm:p-6">
            <TransformWrapper
              initialScale={1}
              minScale={0.1}
              maxScale={4}
              limitToBounds={false}
              centerOnInit={true}
              wheel={{
                step: 0.1,
              }}
              doubleClick={{
                disabled: false,
                step: 0.7,
              }}
            >
              {({ zoomIn, zoomOut, resetTransform }) => (
                <>
                  {/* Zoom Controls */}
                  <div className="mb-4 flex justify-end gap-2">
                    <button
                      onClick={() => zoomIn()}
                      className="px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                      title="Zoom in"
                    >
                      <svg
                        className="w-4 h-4 inline mr-1"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 4v16m8-8H4"
                        />
                      </svg>
                      Zoom In
                    </button>
                    <button
                      onClick={() => zoomOut()}
                      className="px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                      title="Zoom out"
                    >
                      <svg
                        className="w-4 h-4 inline mr-1"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M20 12H4"
                        />
                      </svg>
                      Zoom Out
                    </button>
                    <button
                      onClick={() => resetTransform()}
                      className="px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                      title="Reset zoom"
                    >
                      <svg
                        className="w-4 h-4 inline mr-1"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                        />
                      </svg>
                      Reset
                    </button>
                  </div>

                  {/* Graph Content */}
                  <TransformComponent
                    wrapperClass="w-full"
                    contentClass="flex items-center justify-center"
                  >
                    <div
                      ref={mermaidRef}
                      className="flex justify-center items-center"
                      style={{ minWidth: '800px', minHeight: '600px' }}
                    />
                  </TransformComponent>
                </>
              )}
            </TransformWrapper>
          </div>
        )}
      </div>
    </div>
  )
}

