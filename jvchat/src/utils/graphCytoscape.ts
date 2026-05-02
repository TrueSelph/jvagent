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

interface GraphThemeColors {
  nodeBg: string
  nodeBorder: string
  nodeColor: string
  missingBg: string
  missingBorder: string
  edgeColor: string
  selectedBorder: string
}

function getGraphThemeColors(isDark: boolean): GraphThemeColors {
  return isDark
    ? {
        nodeBg: '#2d3d52',
        nodeBorder: '#3d9cfd',
        nodeColor: '#f1f5f9',
        missingBg: '#5c2d2d',
        missingBorder: '#c94c4c',
        edgeColor: '#64748b',
        selectedBorder: '#93c5fd',
      }
    : {
        nodeBg: '#e2e8f0',
        nodeBorder: '#6366f1',
        nodeColor: '#1e293b',
        missingBg: '#fecaca',
        missingBorder: '#dc2626',
        edgeColor: '#94a3b8',
        selectedBorder: '#4f46e5',
      }
}

export function buildGraphStylesheet(theme: 'light' | 'dark') {
  const isDark = theme === 'dark'
  const c = getGraphThemeColors(isDark)

  return [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': '11px',
        color: c.nodeColor,
        'background-color': c.nodeBg,
        'border-width': 1,
        'border-color': c.nodeBorder,
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
        'border-color': c.selectedBorder,
      },
    },
    {
      selector: 'node[?missing]',
      style: {
        'background-color': c.missingBg,
        'border-color': c.missingBorder,
      },
    },
    {
      selector: 'edge',
      style: {
        width: 2,
        'line-color': c.edgeColor,
        'target-arrow-color': c.edgeColor,
        'source-arrow-color': c.edgeColor,
        'target-arrow-shape': 'triangle',
        'target-arrow-fill': 'filled',
        'curve-style': 'bezier',
        'arrow-scale': 0.95,
        label: 'data(elabel)',
        'font-size': '8px',
        color: c.edgeColor,
        'text-background-color': isDark ? '#0f172a' : '#f8fafc',
        'text-background-opacity': 0.92,
        'text-background-padding': '2px',
        'text-border-color': c.edgeColor,
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
        'line-color': c.selectedBorder,
        'target-arrow-color': c.selectedBorder,
        'source-arrow-color': c.selectedBorder,
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

const LAYOUT_FIT_PADDING = 48

/** Fit viewport to all graph elements; safe if empty. */
function fitAfterLayout(cy: Core): void {
  try {
    cy.fit(undefined, LAYOUT_FIT_PADDING)
  } catch {
    /* empty or no extent */
  }
}

/** Run a layout, then fit the viewport (required so Root/App are not off-screen). */
function runLayoutWithFit(
  cy: Core,
  layoutOptions: Record<string, unknown>,
  animate: boolean
): void {
  const layout = cy.layout({
    ...layoutOptions,
    animate,
    animationDuration: animate ? 280 : 0,
  } as never)
  if (animate) {
    layout.one('layoutstop', () => fitAfterLayout(cy))
  }
  layout.run()
  if (!animate) {
    fitAfterLayout(cy)
  }
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
  runLayoutWithFit(
    cy,
    {
      name: 'dagre',
      rankDir,
      spacingFactor,
      ...(nodeSep != null ? { nodeSep } : {}),
      ...(edgeSep != null ? { edgeSep } : {}),
      ...(rankSep != null ? { rankSep } : {}),
    },
    animate
  )
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
    runLayoutWithFit(
      cy,
      {
        name: 'breadthfirst',
        directed: true,
        roots,
        spacingFactor: 1.35,
        avoidOverlap: true,
      },
      animate
    )
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
  const c = getGraphThemeColors(theme === 'dark')

  cy.style()
    .selector('node')
    .style({
      color: c.nodeColor,
      'background-color': c.nodeBg,
      'border-color': c.nodeBorder,
    })
    .selector('node:selected')
    .style({
      'border-color': c.selectedBorder,
    })
    .selector('node[?missing]')
    .style({
      'background-color': c.missingBg,
      'border-color': c.missingBorder,
    })
    .selector('edge')
    .style({
      'line-color': c.edgeColor,
      'target-arrow-color': c.edgeColor,
      'source-arrow-color': c.edgeColor,
      color: c.edgeColor,
    })
    .selector('edge:selected')
    .style({
      'line-color': c.selectedBorder,
      'target-arrow-color': c.selectedBorder,
      'source-arrow-color': c.selectedBorder,
    })
    .update()
}
