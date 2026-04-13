import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import * as d3 from 'd3'
import { graphviz } from 'd3-graphviz'
import type { Core } from 'cytoscape'
import { apiClient } from '../config/api'
import { useTheme } from '../context/ThemeContext'
import type { GraphExpandResponse, GraphVizEdge, GraphVizNode } from '../types/api'
import {
  applyThemeToCy,
  createProgressiveGraphCy,
  destroyCy,
  mergePayloadIntoCy,
  payloadToElements,
  runGraphLayout,
  type GraphLayoutPreset,
} from '../utils/graphCytoscape'

interface GraphViewerProps {
  onClose?: () => void
  isEmbedded?: boolean
}

const EXPAND_LIMIT = 40
const SUBGRAPH_ROOT = 'n.Root.root'

const INSPECTOR_WIDTH_LS_KEY = 'jvchat_graph_inspector_width'
const INSPECTOR_WIDTH_DEFAULT = 400
const INSPECTOR_WIDTH_MIN = 260
const INSPECTOR_WIDTH_MAX_ABS = 720
const INSPECTOR_WIDTH_MAX_FRAC = 0.68

function readStoredInspectorWidth(): number {
  try {
    const raw = localStorage.getItem(INSPECTOR_WIDTH_LS_KEY)
    if (raw == null) return INSPECTOR_WIDTH_DEFAULT
    const n = parseInt(raw, 10)
    if (Number.isFinite(n)) {
      return Math.min(
        INSPECTOR_WIDTH_MAX_ABS,
        Math.max(INSPECTOR_WIDTH_MIN, n)
      )
    }
  } catch {
    /* ignore */
  }
  return INSPECTOR_WIDTH_DEFAULT
}

type SelectedElement = { kind: 'node' | 'edge'; id: string } | null

type GraphExpandModel = {
  baseNodeIds: Set<string>
  baseEdgeIds: Set<string>
  refCounts: Map<string, number>
  expandBatches: Map<string, Set<string>[]>
  expandedCenters: Set<string>
}

function createExpandModel(): GraphExpandModel {
  return {
    baseNodeIds: new Set(),
    baseEdgeIds: new Set(),
    refCounts: new Map(),
    expandBatches: new Map(),
    expandedCenters: new Set(),
  }
}

function resetExpandModel(
  m: GraphExpandModel,
  nodes: GraphVizNode[],
  edges: GraphVizEdge[]
): void {
  m.baseNodeIds = new Set(nodes.map((n) => n.id))
  m.baseEdgeIds = new Set(edges.map((e) => e.id))
  m.refCounts.clear()
  m.expandBatches.clear()
  m.expandedCenters.clear()
}

/** Non-base ids appearing anywhere in an expand response (for ref counting). */
function collectNonBaseIdsFromExpand(ex: GraphExpandResponse, m: GraphExpandModel): Set<string> {
  const out = new Set<string>()
  for (const n of ex.nodes) {
    if (!m.baseNodeIds.has(n.id)) out.add(n.id)
  }
  for (const e of ex.edges) {
    if (!m.baseEdgeIds.has(e.id)) out.add(e.id)
  }
  return out
}

function registerExpandBatch(
  m: GraphExpandModel,
  centerId: string,
  batch: Set<string>
): boolean {
  if (batch.size === 0) return false
  for (const id of batch) {
    m.refCounts.set(id, (m.refCounts.get(id) || 0) + 1)
  }
  if (!m.expandBatches.has(centerId)) m.expandBatches.set(centerId, [])
  m.expandBatches.get(centerId)!.push(new Set(batch))
  m.expandedCenters.add(centerId)
  return true
}

/** Retract one center’s expand batches; returns ids removed from graph. */
function retractExpandCenter(cy: Core, m: GraphExpandModel, centerId: string): string[] {
  const batches = m.expandBatches.get(centerId)
  if (!batches) return []
  const toRemove = new Set<string>()
  for (const batch of batches) {
    for (const id of batch) {
      if (m.baseNodeIds.has(id) || m.baseEdgeIds.has(id)) continue
      const next = (m.refCounts.get(id) || 0) - 1
      if (next <= 0) {
        m.refCounts.delete(id)
        toRemove.add(id)
      } else {
        m.refCounts.set(id, next)
      }
    }
  }
  m.expandBatches.delete(centerId)
  m.expandedCenters.delete(centerId)

  const edgeEls = cy.collection()
  const nodeEls = cy.collection()
  for (const id of toRemove) {
    const el = cy.getElementById(id)
    if (el.empty()) continue
    const group = el.group()
    if (group === 'edges') edgeEls.merge(el)
    else if (group === 'nodes') nodeEls.merge(el)
  }
  if (!edgeEls.empty()) cy.remove(edgeEls)
  if (!nodeEls.empty()) cy.remove(nodeEls)
  return [...toRemove]
}

function mergeNodeRecord(a: GraphVizNode | undefined, b: GraphVizNode): GraphVizNode {
  if (!a) return { ...b }
  return {
    ...a,
    ...b,
    context:
      b.context != null && Object.keys(b.context).length > 0
        ? { ...a.context, ...b.context }
        : a.context ?? b.context,
  }
}

function mergeEdgeRecord(a: GraphVizEdge | undefined, b: GraphVizEdge): GraphVizEdge {
  if (!a) return { ...b }
  return {
    ...a,
    ...b,
    context:
      b.context != null && Object.keys(b.context).length > 0
        ? { ...a.context, ...b.context }
        : a.context ?? b.context,
  }
}

function upsertNodes(
  prev: Record<string, GraphVizNode>,
  incoming: GraphVizNode[]
): Record<string, GraphVizNode> {
  const next = { ...prev }
  for (const n of incoming) {
    next[n.id] = mergeNodeRecord(next[n.id], n)
  }
  return next
}

function upsertEdges(
  prev: Record<string, GraphVizEdge>,
  incoming: GraphVizEdge[]
): Record<string, GraphVizEdge> {
  const next = { ...prev }
  for (const e of incoming) {
    next[e.id] = mergeEdgeRecord(next[e.id], e)
  }
  return next
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

function isAxios404(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 404
}

function layoutPresetLabel(p: GraphLayoutPreset): string {
  if (p === 'dagre-lr') return 'Horizontal'
  if (p === 'dagre-tb') return 'Vertical'
  return 'Tree'
}

export function GraphViewer({ onClose, isEmbedded = false }: GraphViewerProps) {
  const { theme } = useTheme()
  const cyTheme = theme === 'dark' ? 'dark' : 'light'
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [graphMode, setGraphMode] = useState<'progressive' | 'dot'>('progressive')
  const [graphData, setGraphData] = useState<string | null>(null)
  const [progressiveNodes, setProgressiveNodes] = useState<GraphVizNode[]>([])
  const [progressiveEdges, setProgressiveEdges] = useState<GraphVizEdge[]>([])
  const [repairing, setRepairing] = useState(false)
  const [repairMessage, setRepairMessage] = useState<string | null>(null)
  const [repairError, setRepairError] = useState<string | null>(null)
  const [expandPagination, setExpandPagination] = useState<{
    nodeId: string
    nextCursor: number
  } | null>(null)
  const [expandBusy, setExpandBusy] = useState(false)

  const [detailLevel, setDetailLevel] = useState<'summary' | 'full'>('full')
  const [layoutPreset, setLayoutPreset] = useState<GraphLayoutPreset>('dagre-lr')
  const [selectedElement, setSelectedElement] = useState<SelectedElement>(null)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [inspectorWidthPx, setInspectorWidthPx] = useState(readStoredInspectorWidth)
  const [isLargeSplitLayout, setIsLargeSplitLayout] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(min-width: 1024px)').matches
  )
  const [nodeById, setNodeById] = useState<Record<string, GraphVizNode>>({})
  const [edgeById, setEdgeById] = useState<Record<string, GraphVizEdge>>({})

  const containerRef = useRef<HTMLDivElement>(null)
  const splitRowRef = useRef<HTMLDivElement>(null)
  const graphvizRef = useRef<ReturnType<typeof graphviz> | null>(null)
  const cyRef = useRef<Core | null>(null)
  const expandModelRef = useRef<GraphExpandModel>(createExpandModel())
  const cyHandlersRef = useRef<{
    onTapBackground: () => void
    onTapNode: (id: string) => void
    onTapEdge: (id: string) => void
    onDblTapNode: (id: string) => void
  } | null>(null)

  const navigate = useNavigate()

  const fetchGraph = useCallback(async () => {
    setLoading(true)
    setError(null)
    setExpandPagination(null)
    setSelectedElement(null)
    destroyCy(cyRef.current)
    cyRef.current = null
    graphvizRef.current = null
    resetExpandModel(expandModelRef.current, [], [])
    setNodeById({})
    setEdgeById({})

    try {
      const sub = await apiClient.getGraphSubgraph({
        root: SUBGRAPH_ROOT,
        max_depth: 2,
        max_nodes: 150,
        max_edges_per_node: 100,
        detail_level: detailLevel,
      })
      setGraphMode('progressive')
      setGraphData(null)
      setProgressiveNodes(sub.nodes)
      setProgressiveEdges(sub.edges)
      resetExpandModel(expandModelRef.current, sub.nodes, sub.edges)
      const nMap: Record<string, GraphVizNode> = {}
      const eMap: Record<string, GraphVizEdge> = {}
      for (const n of sub.nodes) nMap[n.id] = n
      for (const e of sub.edges) eMap[e.id] = e
      setNodeById(nMap)
      setEdgeById(eMap)
    } catch (err: unknown) {
      if (isAxios404(err)) {
        try {
          const dot = await apiClient.getGraph('dot', true)
          setGraphMode('dot')
          setGraphData(dot)
          setProgressiveNodes([])
          setProgressiveEdges([])
        } catch (dotErr: unknown) {
          console.error('Failed to fetch graph (DOT fallback):', dotErr)
          setError(dotErr instanceof Error ? dotErr.message : 'Failed to load graph data')
        }
      } else {
        console.error('Failed to fetch graph:', err)
        setError(err instanceof Error ? err.message : 'Failed to load graph data')
      }
    } finally {
      setLoading(false)
    }
  }, [detailLevel])

  useEffect(() => {
    void fetchGraph()
  }, [fetchGraph])

  const applyLayout = useCallback(
    (animate = true) => {
      const cy = cyRef.current
      if (!cy || graphMode !== 'progressive') return
      runGraphLayout(cy, layoutPreset, SUBGRAPH_ROOT, animate)
    },
    [graphMode, layoutPreset]
  )

  const expandOrRetractNode = useCallback(
    async (nodeId: string, cursor = 0) => {
      const cy = cyRef.current
      if (!cy || graphMode !== 'progressive') return
      const m = expandModelRef.current

      if (cursor === 0 && m.expandedCenters.has(nodeId)) {
        const removed = retractExpandCenter(cy, m, nodeId)
        setNodeById((prev) => {
          const next = { ...prev }
          for (const id of removed) delete next[id]
          return next
        })
        setEdgeById((prev) => {
          const next = { ...prev }
          for (const id of removed) delete next[id]
          return next
        })
        setSelectedElement((sel) =>
          sel && removed.includes(sel.id) ? null : sel
        )
        setExpandPagination((p) => (p?.nodeId === nodeId ? null : p))
        applyLayout(true)
        return
      }

      setExpandBusy(true)
      try {
        const ex = await apiClient.getGraphExpand({
          node_id: nodeId,
          limit: EXPAND_LIMIT,
          cursor,
          detail_level: detailLevel,
        })
        if (!ex.found) return

        const batch = collectNonBaseIdsFromExpand(ex, m)
        const hadNew = registerExpandBatch(m, nodeId, batch)

        mergePayloadIntoCy(cy, ex.nodes, ex.edges)
        setNodeById((prev) => upsertNodes(prev, ex.nodes))
        setEdgeById((prev) => upsertEdges(prev, ex.edges))

        if (hadNew || cursor > 0) {
          applyLayout(true)
        }

        if (ex.pagination.has_more && ex.pagination.next_cursor != null) {
          setExpandPagination({ nodeId, nextCursor: ex.pagination.next_cursor })
        } else {
          setExpandPagination(null)
        }
      } catch (e: unknown) {
        console.error('Expand node failed:', e)
        setError(e instanceof Error ? e.message : 'Failed to expand node')
      } finally {
        setExpandBusy(false)
      }
    },
    [graphMode, detailLevel, applyLayout]
  )

  cyHandlersRef.current = {
    onTapBackground: () => {
      cyRef.current?.nodes().unselect()
      cyRef.current?.edges().unselect()
      setSelectedElement(null)
    },
    onTapNode: (id: string) => {
      cyRef.current?.nodes().unselect()
      cyRef.current?.edges().unselect()
      cyRef.current?.$(`#${CSS.escape(id)}`).select()
      setSelectedElement({ kind: 'node', id })
    },
    onTapEdge: (id: string) => {
      cyRef.current?.nodes().unselect()
      cyRef.current?.edges().unselect()
      cyRef.current?.$(`#${CSS.escape(id)}`).select()
      setSelectedElement({ kind: 'edge', id })
    },
    onDblTapNode: (id: string) => {
      setExpandPagination(null)
      void expandOrRetractNode(id, 0)
    },
  }

  useEffect(() => {
    if (graphMode !== 'progressive' || loading || !containerRef.current) {
      return
    }

    const container = containerRef.current
    d3.select(container).selectAll('*').remove()
    destroyCy(cyRef.current)
    cyRef.current = null

    if (progressiveNodes.length === 0 && progressiveEdges.length === 0) {
      return
    }

    const cy = createProgressiveGraphCy({
      container,
      theme: cyTheme,
      elements: payloadToElements(progressiveNodes, progressiveEdges),
      initialLayout: layoutPreset,
      rootId: SUBGRAPH_ROOT,
    })
    cyRef.current = cy

    const bg = () => cyHandlersRef.current?.onTapBackground()
    const tn = (evt: cytoscape.EventObject) =>
      cyHandlersRef.current?.onTapNode(evt.target.id())
    const te = (evt: cytoscape.EventObject) =>
      cyHandlersRef.current?.onTapEdge(evt.target.id())
    const dn = (evt: cytoscape.EventObject) =>
      cyHandlersRef.current?.onDblTapNode(evt.target.id())

    cy.on('tap', bg)
    cy.on('tap', 'node', tn)
    cy.on('tap', 'edge', te)
    cy.on('dbltap', 'node', dn)

    let resizeTimeout: ReturnType<typeof setTimeout> | undefined
    const ro = new ResizeObserver(() => {
      clearTimeout(resizeTimeout)
      resizeTimeout = setTimeout(() => {
        cyRef.current?.resize()
      }, 120)
    })
    ro.observe(container)

    return () => {
      cy.removeListener('tap', bg)
      cy.removeListener('tap', 'node', tn)
      cy.removeListener('tap', 'edge', te)
      cy.removeListener('dbltap', 'node', dn)
      clearTimeout(resizeTimeout)
      ro.disconnect()
      destroyCy(cyRef.current)
      cyRef.current = null
    }
  }, [graphMode, loading, progressiveNodes, progressiveEdges, cyTheme, layoutPreset])

  useEffect(() => {
    if (graphMode !== 'progressive' || !cyRef.current) return
    applyThemeToCy(cyRef.current, cyTheme)
  }, [theme, graphMode, cyTheme])

  useEffect(() => {
    if (graphMode !== 'progressive' || loading || !cyRef.current) return
    applyLayout(true)
  }, [layoutPreset, graphMode, loading, applyLayout])

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
    if (graphMode !== 'dot' || !graphData || !containerRef.current || loading) return

    const container = containerRef.current
    destroyCy(cyRef.current)
    cyRef.current = null
    d3.select(container).selectAll('*').remove()

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
        const ds = theme === 'dark' ? applyDarkModeDot(graphData) : graphData
        graphvizRef.current.width(w).height(h).renderDot(ds)
      }, 150)
    })
    resizeObserver.observe(container)

    return () => {
      clearTimeout(resizeTimeout)
      resizeObserver.disconnect()
      graphvizRef.current = null
      d3.select(container).selectAll('*').remove()
    }
  }, [graphData, loading, theme, renderGraph, graphMode])

  const handleZoomIn = () => {
    if (graphMode === 'progressive' && cyRef.current) {
      const z = cyRef.current.zoom()
      cyRef.current.zoom(z * 1.25)
      return
    }
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.scaleBy(zoomSelection, 1.3)
    }
  }

  const handleZoomOut = () => {
    if (graphMode === 'progressive' && cyRef.current) {
      const z = cyRef.current.zoom()
      cyRef.current.zoom(z / 1.25)
      return
    }
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.scaleBy(zoomSelection, 1 / 1.3)
    }
  }

  const handleResetZoom = () => {
    if (graphMode === 'progressive' && cyRef.current) {
      cyRef.current.fit(undefined, 48)
      return
    }
    const gv = graphvizRef.current
    const zoomBehavior = gv?.zoomBehavior()
    const zoomSelection = gv?.zoomSelection()
    if (zoomBehavior && zoomSelection) {
      zoomBehavior.transform(zoomSelection, d3.zoomIdentity)
    }
  }

  const handleRefresh = () => {
    void fetchGraph()
  }

  const handleLoadMoreNeighbors = () => {
    if (!expandPagination) return
    void expandOrRetractNode(expandPagination.nodeId, expandPagination.nextCursor)
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
      ['interactions_pruned', 'interaction(s) pruned (rolling limit)'],
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
      await fetchGraph()
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
    const mq = window.matchMedia('(min-width: 1024px)')
    const apply = () => setIsLargeSplitLayout(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  const handleInspectorResizeMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      if (e.button !== 0) return
      const startX = e.clientX
      const startW = inspectorWidthPx
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      const clampW = (raw: number) => {
        const rowW = splitRowRef.current?.getBoundingClientRect().width ?? 1400
        const cap = Math.min(
          INSPECTOR_WIDTH_MAX_ABS,
          Math.floor(rowW * INSPECTOR_WIDTH_MAX_FRAC)
        )
        return Math.min(cap, Math.max(INSPECTOR_WIDTH_MIN, raw))
      }

      let lastW = startW

      const onMove = (ev: MouseEvent) => {
        lastW = clampW(startW + startX - ev.clientX)
        setInspectorWidthPx(lastW)
      }

      const onUp = () => {
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        try {
          localStorage.setItem(INSPECTOR_WIDTH_LS_KEY, String(lastW))
        } catch {
          /* ignore */
        }
      }

      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [inspectorWidthPx]
  )

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

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      /* ignore */
    }
  }

  const inspectorBody = (() => {
    if (graphMode !== 'progressive') {
      return <p className="text-sm text-gray-500 dark:text-gray-400">Inspector is for progressive graph mode.</p>
    }
    if (!selectedElement) {
      return (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Tap a node or edge to inspect. Use <strong>Full</strong> detail for context fields.
        </p>
      )
    }
    if (selectedElement.kind === 'node') {
      const n = nodeById[selectedElement.id]
      if (!n) {
        return <p className="text-sm text-amber-600 dark:text-amber-400">No data for this node (reload if needed).</p>
      }
      const ctxJson =
        n.context && Object.keys(n.context).length > 0
          ? JSON.stringify(n.context, null, 2)
          : null
      return (
        <div className="flex flex-col flex-1 min-h-0 gap-2 text-sm">
          <div className="flex-shrink-0 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-gray-900 dark:text-gray-100">Node</span>
              <button
                type="button"
                onClick={() => void copyText(n.id)}
                className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                Copy id
              </button>
            </div>
            <dl className="space-y-0.5 text-gray-700 dark:text-gray-300">
              <div><dt className="text-xs text-gray-500 dark:text-gray-400">id</dt><dd className="font-mono text-xs break-all">{n.id}</dd></div>
              <div><dt className="text-xs text-gray-500 dark:text-gray-400">entity</dt><dd>{n.entity}</dd></div>
              <div><dt className="text-xs text-gray-500 dark:text-gray-400">label</dt><dd>{n.label}</dd></div>
              <div><dt className="text-xs text-gray-500 dark:text-gray-400">degree</dt><dd>{n.degree}</dd></div>
              {n.missing && <div className="text-amber-600 dark:text-amber-400">Missing record</div>}
            </dl>
            {detailLevel === 'summary' && (
              <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-slate-600 pt-2">
                Switch to <strong>Full</strong> and refresh to load <code className="text-[11px]">context</code> from the API.
              </p>
            )}
          </div>
          {detailLevel === 'full' && (
            <div className="flex flex-col flex-1 min-h-0 border-t border-gray-200 dark:border-slate-600 pt-2 overflow-hidden">
              <div className="flex-shrink-0 flex items-center justify-between gap-2 mb-1">
                <span className="text-xs font-medium text-gray-500 dark:text-gray-400">context</span>
                {ctxJson && (
                  <button
                    type="button"
                    onClick={() => void copyText(ctxJson)}
                    className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                  >
                    Copy JSON
                  </button>
                )}
              </div>
              {ctxJson ? (
                <pre className="flex-1 min-h-0 text-xs sm:text-[13px] leading-snug font-mono bg-slate-100 dark:bg-slate-800 p-2 sm:p-3 rounded overflow-auto whitespace-pre-wrap break-all">
                  {ctxJson}
                </pre>
              ) : (
                <p className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">Empty context</p>
              )}
            </div>
          )}
        </div>
      )
    }
    const e = edgeById[selectedElement.id]
    if (!e) {
      return <p className="text-sm text-amber-600 dark:text-amber-400">No data for this edge (reload if needed).</p>
    }
    const ctxJson =
      e.context && Object.keys(e.context).length > 0
        ? JSON.stringify(e.context, null, 2)
        : null
    return (
      <div className="flex flex-col flex-1 min-h-0 gap-2 text-sm">
        <div className="flex-shrink-0 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-gray-900 dark:text-gray-100">Edge</span>
            <button
              type="button"
              onClick={() => void copyText(e.id)}
              className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              Copy id
            </button>
          </div>
          <dl className="space-y-0.5 text-gray-700 dark:text-gray-300">
            <div><dt className="text-xs text-gray-500 dark:text-gray-400">id</dt><dd className="font-mono text-xs break-all">{e.id}</dd></div>
            <div><dt className="text-xs text-gray-500 dark:text-gray-400">entity / label</dt><dd>{e.entity} / {e.label}</dd></div>
            <div><dt className="text-xs text-gray-500 dark:text-gray-400">source → target</dt><dd className="font-mono text-[11px] break-all">{e.source} → {e.target}</dd></div>
            <div><dt className="text-xs text-gray-500 dark:text-gray-400">bidirectional</dt><dd>{String(e.bidirectional)}</dd></div>
            {e.direction != null && (
              <div><dt className="text-xs text-gray-500 dark:text-gray-400">direction (expand)</dt><dd>{e.direction}</dd></div>
            )}
          </dl>
          {detailLevel === 'summary' && (
            <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-slate-600 pt-2">
              Switch to <strong>Full</strong> and refresh for edge <code className="text-[11px]">context</code>.
            </p>
          )}
        </div>
        {detailLevel === 'full' && (
          <div className="flex flex-col flex-1 min-h-0 border-t border-gray-200 dark:border-slate-600 pt-2 overflow-hidden">
            <div className="flex-shrink-0 flex items-center justify-between gap-2 mb-1">
              <span className="text-xs font-medium text-gray-500 dark:text-gray-400">context</span>
              {ctxJson && (
                <button
                  type="button"
                  onClick={() => void copyText(ctxJson)}
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  Copy JSON
                </button>
              )}
            </div>
            {ctxJson ? (
              <pre className="flex-1 min-h-0 text-xs sm:text-[13px] leading-snug font-mono bg-slate-100 dark:bg-slate-800 p-2 sm:p-3 rounded overflow-auto whitespace-pre-wrap break-all">
                {ctxJson}
              </pre>
            ) : (
              <p className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">Empty context</p>
            )}
          </div>
        )}
      </div>
    )
  })()

  const graphControls = (
    <>
      {graphMode === 'progressive' && (
        <>
          {!inspectorOpen && (
            <button
              type="button"
              onClick={() => setInspectorOpen(true)}
              className="hidden lg:inline-flex items-center px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-slate-700"
              title="Show inspector panel"
            >
              Inspector
            </button>
          )}
          <div className="flex rounded-lg border border-gray-200 dark:border-slate-600 overflow-hidden text-xs">
            <button
              type="button"
              onClick={() => setDetailLevel('summary')}
              className={`px-2 py-1.5 sm:px-3 ${detailLevel === 'summary' ? 'bg-indigo-600 text-white' : 'bg-gray-100 dark:bg-slate-700 text-gray-700 dark:text-gray-200'}`}
            >
              Summary
            </button>
            <button
              type="button"
              onClick={() => setDetailLevel('full')}
              className={`px-2 py-1.5 sm:px-3 border-l border-gray-200 dark:border-slate-600 ${detailLevel === 'full' ? 'bg-indigo-600 text-white' : 'bg-gray-100 dark:bg-slate-700 text-gray-700 dark:text-gray-200'}`}
            >
              Full
            </button>
          </div>
          <select
            value={layoutPreset}
            onChange={(e) => setLayoutPreset(e.target.value as GraphLayoutPreset)}
            className="text-xs sm:text-sm rounded-lg border border-gray-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-gray-800 dark:text-gray-200 px-2 py-1.5 max-w-[9rem] sm:max-w-none"
            title="Layout"
            aria-label="Graph layout"
          >
            <option value="dagre-lr">{layoutPresetLabel('dagre-lr')}</option>
            <option value="dagre-tb">{layoutPresetLabel('dagre-tb')}</option>
            <option value="breadthfirst">{layoutPresetLabel('breadthfirst')}</option>
          </select>
        </>
      )}
    </>
  )

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

  const graphHint =
    graphMode === 'progressive'
      ? 'Tap: inspect · Double-click: expand neighbors (again to retract) · Summary/Full reloads data · Layout presets arrange the graph.'
      : 'Classic Graphviz view (server has no JSON graph API). Drag to pan, scroll to zoom.'

  const headerActions = (
    <>
      {graphControls}
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
      {graphMode === 'progressive' && expandPagination && (
        <button
          onClick={handleLoadMoreNeighbors}
          disabled={expandBusy}
          className="px-3 py-2 text-sm text-gray-700 dark:text-gray-300 bg-amber-100 dark:bg-amber-900/40 rounded-lg hover:bg-amber-200 dark:hover:bg-amber-900/60 disabled:opacity-50 transition-colors"
          title="Load next page of edges for the last expanded node"
        >
          {expandBusy ? 'Loading…' : 'Load more neighbors'}
        </button>
      )}
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
    </>
  )

  const inspectorAside =
    graphMode === 'progressive' && inspectorOpen ? (
      <aside
        className="flex flex-col min-h-0 w-full border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden border-t lg:border-t-0 max-h-[min(68vh,32rem)] flex-1 min-h-[12rem] lg:max-h-none lg:flex-none lg:min-h-0"
        style={
          isLargeSplitLayout
            ? {
                width: inspectorWidthPx,
                flexShrink: 0,
                minWidth: INSPECTOR_WIDTH_MIN,
                maxWidth: '68%',
              }
            : undefined
        }
      >
        <div className="flex-shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 dark:border-slate-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Inspector</h3>
          <button
            type="button"
            onClick={() => setInspectorOpen(false)}
            className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-700 flex items-center gap-1"
            title="Collapse inspector"
            aria-label="Collapse inspector"
          >
            <svg className="hidden lg:block w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            <span className="lg:hidden">Hide</span>
          </button>
        </div>
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-3">{inspectorBody}</div>
      </aside>
    ) : null

  const inspectorResizeHandle =
    graphMode === 'progressive' && inspectorOpen && isLargeSplitLayout ? (
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Drag to resize inspector"
        className="group relative z-[1] hidden lg:flex w-px flex-shrink-0 shrink-0 cursor-col-resize touch-none select-none self-stretch justify-center px-1.5 -mx-1.5"
        onMouseDown={handleInspectorResizeMouseDown}
      >
        <span
          className="pointer-events-none w-px min-w-px self-stretch bg-gray-200 group-hover:bg-indigo-400 dark:bg-slate-600 dark:group-hover:bg-indigo-500"
          aria-hidden
        />
      </div>
    ) : null

  const progressiveGraphRow = (
    <div
      ref={splitRowRef}
      className="flex flex-1 min-h-0 flex-col lg:flex-row overflow-hidden"
    >
      <div className="flex-1 min-h-0 min-w-0 relative">
        <div className="absolute top-4 right-4 z-10 flex flex-col gap-2 items-end">
          {zoomControls(true)}
          <button
            type="button"
            onClick={() => setInspectorOpen((o) => !o)}
            className="lg:hidden text-xs px-2 py-1 rounded-md bg-white/90 dark:bg-slate-800/90 border border-gray-200 dark:border-slate-600 shadow"
          >
            {inspectorOpen ? 'Hide details' : 'Details'}
          </button>
        </div>
        {!inspectorOpen && graphMode === 'progressive' && (
          <button
            type="button"
            onClick={() => setInspectorOpen(true)}
            className="hidden lg:flex absolute right-0 top-1/2 -translate-y-1/2 z-20 flex-row items-center gap-1.5 rounded-l-lg border border-r-0 border-gray-200 dark:border-slate-600 bg-white dark:bg-slate-800 shadow-md px-2.5 py-2 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-slate-700"
            aria-label="Show inspector"
          >
            <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Inspector
          </button>
        )}
        <div
          ref={containerRef}
          className="w-full h-full graph-container bg-slate-50 dark:bg-slate-950"
          style={{ minHeight: '400px' }}
        />
      </div>
      {inspectorResizeHandle}
      {inspectorAside}
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
            <div className="flex items-center gap-3 min-w-0">
              <h2 className="text-xl sm:text-2xl font-semibold text-gray-900 dark:text-gray-100 truncate">App Graph</h2>
              <span className="hidden md:inline text-xs text-gray-500 dark:text-gray-400 line-clamp-2">{graphHint}</span>
            </div>
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {headerActions}
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

          <div className="flex-1 overflow-hidden relative min-h-0 flex flex-col">
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-slate-900 z-20">
                <div className="text-center">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 dark:border-indigo-400 mx-auto"></div>
                  <p className="mt-4 text-gray-600 dark:text-gray-400">Loading graph...</p>
                </div>
              </div>
            )}

            {error && (
              <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-slate-900 p-4 z-20">
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

            {!loading && !error && graphMode === 'progressive' && progressiveGraphRow}

            {!loading && !error && graphMode === 'dot' && (
              <div className="flex-1 relative min-h-0">
                <div className="absolute top-4 right-4 z-10">{zoomControls(true)}</div>
                <div
                  ref={containerRef}
                  className="w-full h-full graph-container bg-slate-50 dark:bg-slate-950"
                  style={{ minHeight: '400px' }}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-700 px-4 sm:px-6 py-4">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-gray-900 dark:text-gray-100">App Graph</h1>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 max-w-xl">{graphHint}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">{headerActions}</div>
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

      <div className="flex-1 min-h-0 max-w-7xl mx-auto px-4 sm:px-6 py-6 w-full flex flex-col">
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

        {!loading && !error && graphMode === 'progressive' && (
          <div className="bg-white dark:bg-slate-900 rounded-lg shadow-sm border border-gray-200 dark:border-slate-700 flex-1 min-h-[500px] flex flex-col overflow-hidden">
            {progressiveGraphRow}
          </div>
        )}

        {!loading && !error && graphMode === 'dot' && (
          <div className="bg-white dark:bg-slate-900 rounded-lg shadow-sm border border-gray-200 dark:border-slate-700 p-4 sm:p-6 h-full min-h-[500px] flex flex-col">
            <div className="mb-4 flex justify-end gap-2">{zoomControls(false)}</div>
            <div
              ref={containerRef}
              className="flex-1 min-h-[400px] graph-container bg-slate-50 dark:bg-slate-950 rounded-md border border-gray-100 dark:border-slate-800"
            />
          </div>
        )}
      </div>
    </div>
  )
}
