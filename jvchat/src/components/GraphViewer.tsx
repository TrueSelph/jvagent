import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import * as d3 from 'd3'
import { graphviz } from 'd3-graphviz'
import { apiClient } from '../config/api'
import { useTheme } from '../context/ThemeContext'

interface GraphViewerProps {
  onClose?: () => void
  isEmbedded?: boolean
}

function applyDarkModeDot(dot: string): string {
  return dot
    .replace(
      /\{/,
      '{ bgcolor="#0f172a" fontcolor="#f8fafc" fontname="sans-serif" edge [color="#94a3b8" fontcolor="#ffffff"] node [fontcolor="#f8fafc" fillcolor="#475569" color="#94a3b8"] '
    )
    .replace(
      /("n\.Root\.root"[^\]]*?)fillcolor="[^"]*"/,
      '$1fillcolor="#1e293b"'
    )
}

export function GraphViewer({ onClose, isEmbedded = false }: GraphViewerProps) {
  const { theme } = useTheme()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [graphData, setGraphData] = useState<string | null>(null)
  const [repairing, setRepairing] = useState(false)
  const [repairMessage, setRepairMessage] = useState<string | null>(null)
  const [repairError, setRepairError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const graphvizRef = useRef<ReturnType<typeof graphviz> | null>(null)
  const navigate = useNavigate()

  const fetchGraph = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const data = await apiClient.getGraph('dot', true)
      setGraphData(data)
    } catch (err: unknown) {
      console.error('Failed to fetch graph:', err)
      setError(err instanceof Error ? err.message : 'Failed to load graph data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchGraph()
  }, [fetchGraph])

  const renderGraph = useCallback(
    (container: HTMLDivElement, dotSource: string, width: number, height: number) => {
      const gv = graphviz(container, false)
        .zoom(true)
        .fit(true)
        .width(width)
        .height(height)
        .zoomScaleExtent([0.1, 10])
        .transition(() => d3.transition('graphviz').duration(0))
        .onerror((err: unknown) => {
          console.error('Graphviz rendering error:', err)
          setError('Failed to render graph diagram')
        })

      graphvizRef.current = gv
      gv.renderDot(dotSource)
    },
    []
  )

  useEffect(() => {
    if (!graphData || !containerRef.current || loading) return

    const container = containerRef.current
    const width = container.clientWidth || 800
    const height = container.clientHeight || 600

    const dotSource = theme === 'dark' ? applyDarkModeDot(graphData) : graphData

    renderGraph(container, dotSource, width, height)

    let resizeTimeout: ReturnType<typeof setTimeout> | undefined
    const resizeObserver = new ResizeObserver(() => {
      clearTimeout(resizeTimeout)
      resizeTimeout = setTimeout(() => {
        if (!containerRef.current || !graphvizRef.current || !graphData) return
        const w = containerRef.current.clientWidth || 800
        const h = containerRef.current.clientHeight || 600
        const dotSource = theme === 'dark' ? applyDarkModeDot(graphData) : graphData
        graphvizRef.current.width(w).height(h).renderDot(dotSource)
      }, 150)
    })
    resizeObserver.observe(container)

    return () => {
      clearTimeout(resizeTimeout)
      resizeObserver.disconnect()
      graphvizRef.current = null
      d3.select(container).selectAll('*').remove()
    }
  }, [graphData, loading, theme, renderGraph])

  const handleZoomIn = () => {
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.scaleBy(zoomSelection, 1.3)
    }
  }

  const handleZoomOut = () => {
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.scaleBy(zoomSelection, 1 / 1.3)
    }
  }

  const handleResetZoom = () => {
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.transform(zoomSelection, d3.zoomIdentity)
    }
  }

  const handleRefresh = () => {
    fetchGraph()
  }

  const formatRepairResult = (result: Record<string, unknown> | null | undefined): string => {
    if (!result) return 'Graph repair completed.'
    const items: [string, string][] = [
      ['memory_repair_agents', 'agent(s) memory repaired'],
      ['orphaned_interactions_deleted', 'interaction(s) deleted'],
      ['orphaned_users_reconnected', 'user(s) reconnected'],
      ['dual_edges_removed', 'dual edge(s) removed'],
      ['conversation_first_edges_restored', 'conv-first edge(s) restored'],
      ['conversation_branch_edges_removed', 'conv-branch edge(s) removed'],
      ['dead_edges_removed', 'dead edge(s) removed'],
      ['orphaned_nodes_reattached', 'orphan(s) reattached'],
      ['orphaned_nodes_deleted', 'orphan(s) deleted'],
      ['node_edge_ids_synced', 'node(s) edge_ids synced'],
      ['duplicate_edges_removed', 'duplicate edge(s) removed'],
    ]
    const parts = items.map(([key, label]) => {
      const n = Number(result[key]) || 0
      return `${n} ${label}`
    })
    const actualRepairKeys = items.map(([k]) => k).filter((k) => k !== 'memory_repair_agents')
    const actualRepairsTotal = actualRepairKeys.reduce((sum, key) => sum + (Number(result[key]) || 0), 0)
    if (actualRepairsTotal === 0) return 'No repairs needed.'
    return `Repair completed: ${parts.join(', ')}.`
  }

  const handleRepairGraph = async () => {
    setRepairing(true)
    setRepairMessage(null)
    setRepairError(null)
    try {
      const result = await apiClient.repairGraph()
      setRepairMessage(formatRepairResult(result))
      fetchGraph()
    } catch (err: unknown) {
      setRepairError(err instanceof Error ? err.message : 'Graph repair failed.')
    } finally {
      setRepairing(false)
    }
  }

  const handleClose = () => {
    if (onClose) {
      onClose()
    } else {
      navigate('/agents')
    }
  }

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

  const zoomControls = (vertical = true) => (
    <div
      className={`flex gap-2 bg-white dark:bg-slate-800 rounded-lg shadow-lg border border-gray-200 dark:border-slate-600 p-2 ${vertical ? 'flex-col' : 'flex-row'}`}
    >
      <button
        onClick={handleZoomIn}
        className="p-2 text-gray-700 dark:text-slate-300 hover:text-gray-900 dark:hover:text-slate-100 hover:bg-gray-100 dark:hover:bg-slate-700 rounded transition-colors"
        title="Zoom in"
        aria-label="Zoom in"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
      </button>
      <button
        onClick={handleZoomOut}
        className="p-2 text-gray-700 dark:text-slate-300 hover:text-gray-900 dark:hover:text-slate-100 hover:bg-gray-100 dark:hover:bg-slate-700 rounded transition-colors"
        title="Zoom out"
        aria-label="Zoom out"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
        </svg>
      </button>
      <button
        onClick={handleResetZoom}
        className="p-2 text-gray-700 dark:text-slate-300 hover:text-gray-900 dark:hover:text-slate-100 hover:bg-gray-100 dark:hover:bg-slate-700 rounded transition-colors"
        title="Reset zoom"
        aria-label="Reset zoom"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
          />
        </svg>
      </button>
    </div>
  )

  if (isEmbedded) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 dark:bg-black/70"
        onClick={(e) => {
          if (e.target === e.currentTarget && onClose) {
            onClose()
          }
        }}
      >
        <div
          className="bg-white dark:bg-slate-900 rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex-shrink-0 border-b border-gray-200 dark:border-slate-700 px-4 sm:px-6 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-xl sm:text-2xl font-semibold text-gray-900 dark:text-gray-100">App Graph</h2>
              <span className="hidden sm:inline text-xs text-gray-500 dark:text-gray-400">
                (Drag to pan, scroll to zoom)
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRefresh}
                disabled={loading}
                className="px-3 py-2 text-sm text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
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
              <button
                onClick={handleRepairGraph}
                disabled={repairing || loading}
                className="px-3 py-2 text-sm text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                title="Repair graph"
              >
                <svg
                  className={`w-4 h-4 ${repairing ? 'animate-spin' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M11 4a7 7 0 00-7 7h2a5 5 0 015-5V4zm0 0V2m0 2a7 7 0 017 7h-2a5 5 0 00-5-5V4zM3 11h2m14 0h2M5.636 5.636l1.414 1.414m9.9 9.9l1.414 1.414M11 18v2m0-2a7 7 0 01-7-7H2m9 7a7 7 0 007-7h2"
                  />
                </svg>
                <span className="hidden sm:inline">Repair graph</span>
              </button>
              {onClose && (
                <button
                  onClick={handleClose}
                  className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  title="Close"
                  aria-label="Close graph viewer"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          {(repairMessage || repairError) && (
            <div className={`flex-shrink-0 px-4 sm:px-6 py-2 flex items-center justify-between gap-2 text-sm ${repairError ? 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300' : 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'}`}>
              <span>{repairError ?? repairMessage}</span>
              <button
                onClick={() => { setRepairMessage(null); setRepairError(null) }}
                className="flex-shrink-0 opacity-70 hover:opacity-100"
                aria-label="Dismiss"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}

          <div className="flex-1 overflow-hidden relative min-h-0">
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-slate-900">
                <div className="text-center">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 dark:border-indigo-400 mx-auto"></div>
                  <p className="mt-4 text-gray-600 dark:text-gray-400">Loading graph...</p>
                </div>
              </div>
            )}

            {error && (
              <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-slate-900 p-4">
                <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg p-6 max-w-md w-full">
                  <h3 className="text-lg font-semibold text-red-800 dark:text-red-300 mb-2">Error Loading Graph</h3>
                  <p className="text-red-700 dark:text-red-300 mb-4">{error}</p>
                  <button
                    onClick={handleRefresh}
                    className="w-full px-4 py-2 bg-red-600 dark:bg-red-500 text-white rounded-lg hover:bg-red-700 dark:hover:bg-red-600 transition-colors"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}

            {!loading && !error && (
              <>
                <div className="absolute top-4 right-4 z-10">{zoomControls(true)}</div>
                <div
                  ref={containerRef}
                  className="w-full h-full graph-container"
                  style={{ minHeight: '400px' }}
                />
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-700 px-4 sm:px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-900 dark:text-gray-100">App Graph</h1>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded-lg hover:bg-gray-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
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
            <button
              onClick={handleRepairGraph}
              disabled={repairing || loading}
              className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded-lg hover:bg-gray-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
              title="Repair graph"
            >
              <svg
                className={`w-4 h-4 ${repairing ? 'animate-spin' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M11 4a7 7 0 00-7 7h2a5 5 0 015-5V4zm0 0V2m0 2a7 7 0 017 7h-2a5 5 0 00-5-5V4zM3 11h2m14 0h2M5.636 5.636l1.414 1.414m9.9 9.9l1.414 1.414M11 18v2m0-2a7 7 0 01-7-7H2m9 7a7 7 0 007-7h2"
                />
              </svg>
              Repair graph
            </button>
          </div>
        </div>
      </div>

      {(repairMessage || repairError) && (
        <div className={`px-4 sm:px-6 py-2 flex items-center justify-between gap-2 text-sm ${repairError ? 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300' : 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'}`}>
          <span>{repairError ?? repairMessage}</span>
          <button
            onClick={() => { setRepairMessage(null); setRepairError(null) }}
            className="flex-shrink-0 opacity-70 hover:opacity-100"
            aria-label="Dismiss"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 max-w-7xl mx-auto px-4 sm:px-6 py-6 w-full">
        {loading && (
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 dark:border-indigo-400 mx-auto"></div>
              <p className="mt-4 text-gray-600 dark:text-gray-400">Loading graph...</p>
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg p-6 max-w-md w-full">
              <h3 className="text-lg font-semibold text-red-800 dark:text-red-300 mb-2">Error Loading Graph</h3>
              <p className="text-red-700 dark:text-red-300 mb-4">{error}</p>
              <button
                onClick={handleRefresh}
                className="w-full px-4 py-2 bg-red-600 dark:bg-red-500 text-white rounded-lg hover:bg-red-700 dark:hover:bg-red-600 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {!loading && !error && (
          <div className="bg-white dark:bg-slate-900 rounded-lg shadow-sm border border-gray-200 dark:border-slate-700 p-4 sm:p-6 h-full min-h-[500px] flex flex-col">
            <div className="mb-4 flex justify-end gap-2">{zoomControls(false)}</div>
            <div ref={containerRef} className="flex-1 min-h-[400px] graph-container" />
          </div>
        )}
      </div>
    </div>
  )
}
