/**
 * Cytoscape + dagre helpers for progressive jvspatial graph JSON payloads.
 */

import cytoscape, { Core, ElementDefinition } from 'cytoscape'
import cytoscapeDagre from 'cytoscape-dagre'
import type { GraphVizEdge, GraphVizNode } from '../types/api'

let dagreRegistered = false

export function ensureCytoscapeDagre(): void {
  if (!dagreRegistered) {
    cytoscape.use(cytoscapeDagre)
    dagreRegistered = true
  }
}

const LABEL_MAX = 48

function truncateLabel(s: string): string {
  if (s.length <= LABEL_MAX) return s
  return `${s.slice(0, LABEL_MAX - 3)}...`
}

export function payloadToElements(
  nodes: GraphVizNode[],
  edges: GraphVizEdge[]
): ElementDefinition[] {
  const els: ElementDefinition[] = []
  for (const n of nodes) {
    els.push({
      data: {
        id: n.id,
        label: truncateLabel(n.label || n.id),
        missing: n.missing === true,
      },
    })
  }
  for (const e of edges) {
    const entity = (e.entity && e.entity.trim()) || 'Edge'
    const elabelRaw = (e.label && e.label.trim()) || entity
    els.push({
      data: {
        id: e.id,
        source: e.source,
        target: e.target,
        bidirectional: e.bidirectional,
        elabel: truncateLabel(elabelRaw || 'Edge'),
        entity,
        ...(e.direction != null ? { direction: e.direction } : {}),
      },
    })
  }
  return els
}

export function buildGraphStylesheet(theme: 'light' | 'dark') {
  const isDark = theme === 'dark'
  const nodeBg = isDark ? '#2d3d52' : '#e2e8f0'
  const nodeBorder = isDark ? '#3d9cfd' : '#6366f1'
  const nodeColor = isDark ? '#f1f5f9' : '#1e293b'
  const missingBg = isDark ? '#5c2d2d' : '#fecaca'
  const missingBorder = isDark ? '#c94c4c' : '#dc2626'
  const edgeColor = isDark ? '#64748b' : '#94a3b8'

  return [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': '11px',
        color: nodeColor,
        'background-color': nodeBg,
        'border-width': 1,
        'border-color': nodeBorder,
        width: 'label',
        height: 32,
        shape: 'roundrectangle',
        padding: '8px',
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-width': 3,
        'border-color': isDark ? '#93c5fd' : '#4f46e5',
      },
    },
    {
      selector: 'node[?missing]',
      style: {
        'background-color': missingBg,
        'border-color': missingBorder,
      },
    },
    {
      selector: 'edge',
      style: {
        width: 2,
        'line-color': edgeColor,
        'target-arrow-color': edgeColor,
        'source-arrow-color': edgeColor,
        'target-arrow-shape': 'triangle',
        'target-arrow-fill': 'filled',
        'curve-style': 'bezier',
        'arrow-scale': 0.95,
        label: 'data(elabel)',
        'font-size': '8px',
        color: edgeColor,
        'text-background-color': isDark ? '#0f172a' : '#f8fafc',
        'text-background-opacity': 0.92,
        'text-background-padding': '2px',
        'text-border-color': edgeColor,
        'text-border-width': 1,
        'text-border-opacity': 0.35,
      },
    },
    {
      selector: 'edge[?bidirectional]',
      style: {
        'source-arrow-shape': 'triangle',
        'source-arrow-fill': 'filled',
        'target-arrow-shape': 'triangle',
        'target-arrow-fill': 'filled',
        'arrow-scale': 0.88,
      },
    },
    {
      selector: 'edge:selected',
      style: {
        width: 3,
        'line-color': isDark ? '#93c5fd' : '#4f46e5',
        'target-arrow-color': isDark ? '#93c5fd' : '#4f46e5',
        'source-arrow-color': isDark ? '#93c5fd' : '#4f46e5',
      },
    },
  ]
}

export function mergePayloadIntoCy(
  cy: Core,
  nodes: GraphVizNode[],
  edges: GraphVizEdge[]
): number {
  const nodeIds = new Set(cy.nodes().map((n) => n.id()))
  const edgeIds = new Set(cy.edges().map((e) => e.id()))
  const toAdd: ElementDefinition[] = []
  for (const n of nodes) {
    if (!nodeIds.has(n.id)) {
      toAdd.push({
        data: {
          id: n.id,
          label: truncateLabel(n.label || n.id),
          missing: n.missing === true,
        },
      })
    }
  }
  for (const e of edges) {
    if (!edgeIds.has(e.id)) {
      const entity = (e.entity && e.entity.trim()) || 'Edge'
      const elabelRaw = (e.label && e.label.trim()) || entity
      toAdd.push({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          bidirectional: e.bidirectional,
          elabel: truncateLabel(elabelRaw || 'Edge'),
          entity,
          ...(e.direction != null ? { direction: e.direction } : {}),
        },
      })
    }
  }
  if (toAdd.length) {
    cy.add(toAdd)
  }
  return toAdd.length
}

export type GraphLayoutPreset = 'dagre-lr' | 'dagre-tb' | 'breadthfirst'

export type DagreLayoutOptions = {
  rankDir: 'LR' | 'TB' | 'BT' | 'RL'
  spacingFactor?: number
  nodeSep?: number
  edgeSep?: number
  rankSep?: number
}

export function runDagreLayout(
  cy: Core,
  animate = true,
  options: DagreLayoutOptions = { rankDir: 'LR', spacingFactor: 1.2 }
): void {
  const {
    rankDir,
    spacingFactor = 1.2,
    nodeSep,
    edgeSep,
    rankSep,
  } = options
  cy.layout({
    name: 'dagre',
    rankDir,
    spacingFactor,
    ...(nodeSep != null ? { nodeSep } : {}),
    ...(edgeSep != null ? { edgeSep } : {}),
    ...(rankSep != null ? { rankSep } : {}),
    animate,
    animationDuration: animate ? 280 : 0,
  } as never).run()
}

/** Apply dagre LR/TB or breadthfirst tree from ``n.Root.root`` (or first node if missing). */
export function runGraphLayout(
  cy: Core,
  preset: GraphLayoutPreset,
  rootId: string,
  animate = true
): void {
  if (cy.nodes().length === 0) return

  if (preset === 'breadthfirst') {
    const root = cy.getElementById(rootId)
    const roots =
      root.nonempty() && root.isNode()
        ? root
        : cy.nodes().first()
    cy.layout({
      name: 'breadthfirst',
      directed: true,
      roots,
      spacingFactor: 1.35,
      avoidOverlap: true,
      animate,
      animationDuration: animate ? 280 : 0,
    } as never).run()
    return
  }

  const rankDir = preset === 'dagre-tb' ? 'TB' : 'LR'
  runDagreLayout(cy, animate, {
    rankDir,
    spacingFactor: rankDir === 'TB' ? 1.25 : 1.2,
    nodeSep: rankDir === 'TB' ? 28 : 36,
    rankSep: rankDir === 'TB' ? 48 : 64,
  })
}

export type GraphCyCreateOptions = {
  container: HTMLElement
  theme: 'light' | 'dark'
  elements: ElementDefinition[]
  /** Initial layout preset (dagre-lr matches historical default). */
  initialLayout?: GraphLayoutPreset
  rootId?: string
}

export function createProgressiveGraphCy(options: GraphCyCreateOptions): Core {
  ensureCytoscapeDagre()
  const cy = cytoscape({
    container: options.container,
    elements: options.elements,
    style: buildGraphStylesheet(options.theme) as never,
    layout: { name: 'preset' } as never,
    wheelSensitivity: 0.35,
    minZoom: 0.08,
    maxZoom: 4,
  })

  const preset = options.initialLayout ?? 'dagre-lr'
  const root = options.rootId ?? 'n.Root.root'
  runGraphLayout(cy, preset, root, false)

  return cy
}

export function destroyCy(cy: Core | null): void {
  if (cy) {
    cy.destroy()
  }
}

export function applyThemeToCy(cy: Core, theme: 'light' | 'dark'): void {
  const isDark = theme === 'dark'
  const nodeBg = isDark ? '#2d3d52' : '#e2e8f0'
  const nodeBorder = isDark ? '#3d9cfd' : '#6366f1'
  const nodeColor = isDark ? '#f1f5f9' : '#1e293b'
  const missingBg = isDark ? '#5c2d2d' : '#fecaca'
  const missingBorder = isDark ? '#c94c4c' : '#dc2626'
  const edgeColor = isDark ? '#64748b' : '#94a3b8'

  cy.style()
    .selector('node')
    .style({
      color: nodeColor,
      'background-color': nodeBg,
      'border-color': nodeBorder,
    })
    .selector('node:selected')
    .style({
      'border-color': isDark ? '#93c5fd' : '#4f46e5',
    })
    .selector('node[?missing]')
    .style({
      'background-color': missingBg,
      'border-color': missingBorder,
    })
    .selector('edge')
    .style({
      'line-color': edgeColor,
      'target-arrow-color': edgeColor,
      'source-arrow-color': edgeColor,
      color: edgeColor,
    })
    .selector('edge:selected')
    .style({
      'line-color': isDark ? '#93c5fd' : '#4f46e5',
      'target-arrow-color': isDark ? '#93c5fd' : '#4f46e5',
      'source-arrow-color': isDark ? '#93c5fd' : '#4f46e5',
    })
    .update()
}
