import type { AxiosInstance } from 'axios'
import type { GraphExpandResponse, GraphSubgraphResponse } from '../../types/api'

export interface GraphServiceContext {
  client: AxiosInstance
  _withFallback: <T>(fn: (baseURL: string) => Promise<T>) => Promise<T>
}

export async function getGraph(
  ctx: GraphServiceContext,
  format = 'dot',
  includeAttributes = true,
): Promise<string> {
  const params = { format, include_attributes: includeAttributes }
  try {
    const response = await ctx._withFallback(async (baseURL) => {
      try {
        return await ctx.client.get('/api/graph', { params, baseURL, responseType: 'text' })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await ctx.client.get('/graph', { params, baseURL, responseType: 'text' })
        }
        throw err
      }
    })
    return response.data as string
  } catch (error: any) {
    const msg = error.response?.data || error.message || 'Failed to fetch graph data'
    throw new Error(typeof msg === 'string' ? msg : 'Failed to fetch graph data')
  }
}

export async function getGraphSubgraph(
  ctx: GraphServiceContext,
  params: {
    root?: string
    max_depth?: number
    max_nodes?: number
    max_edges_per_node?: number
    detail_level?: 'summary' | 'full'
  },
): Promise<GraphSubgraphResponse> {
  const query: Record<string, string | number> = {
    root: params.root ?? 'n.Root.root',
    max_depth: params.max_depth ?? 2,
    max_nodes: params.max_nodes ?? 150,
    max_edges_per_node: params.max_edges_per_node ?? 100,
    detail_level: params.detail_level ?? 'full',
  }
  const response = await ctx._withFallback(async (baseURL) => {
    try {
      return await ctx.client.get<GraphSubgraphResponse>('/api/graph/subgraph', { params: query, baseURL })
    } catch (err: any) {
      if (err.response?.status === 404) {
        return await ctx.client.get<GraphSubgraphResponse>('/graph/subgraph', { params: query, baseURL })
      }
      throw err
    }
  })
  return response.data as GraphSubgraphResponse
}

export async function getGraphExpand(
  ctx: GraphServiceContext,
  params: {
    node_id: string
    direction?: string
    limit?: number
    cursor?: number
    detail_level?: 'summary' | 'full'
  },
): Promise<GraphExpandResponse> {
  const query: Record<string, string | number> = {
    node_id: params.node_id,
    direction: params.direction ?? 'both',
    limit: params.limit ?? 50,
    cursor: params.cursor ?? 0,
    detail_level: params.detail_level ?? 'full',
  }
  const response = await ctx._withFallback(async (baseURL) => {
    try {
      return await ctx.client.get<GraphExpandResponse>('/api/graph/expand', { params: query, baseURL })
    } catch (err: any) {
      if (err.response?.status === 404) {
        return await ctx.client.get<GraphExpandResponse>('/graph/expand', { params: query, baseURL })
      }
      throw err
    }
  })
  return response.data as GraphExpandResponse
}

export async function repairGraph(
  ctx: GraphServiceContext,
  options?: {
    dry_run?: boolean
    recent_minutes?: number
    max_seconds?: number
  },
): Promise<any> {
  try {
    const params: Record<string, string | number | boolean> = {}
    if (options?.dry_run !== undefined) params.dry_run = options.dry_run
    if (options?.recent_minutes !== undefined) params.recent_minutes = options.recent_minutes
    if (options?.max_seconds !== undefined) params.max_seconds = options.max_seconds

    const response = await ctx._withFallback(async (baseURL) => {
      try {
        return await ctx.client.post('/api/graph/repair', {}, { params, baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await ctx.client.post('/graph/repair', {}, { params, baseURL })
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  } catch (error: any) {
    const status = error.response?.status
    let msg: string | unknown = error.response?.data?.detail ?? error.response?.data?.message ?? error.message ?? 'Failed to repair graph'
    if (Array.isArray(msg)) {
      msg = msg.map((e: { msg?: string; message?: string }) => e?.msg ?? e?.message ?? String(e)).join('; ')
    }
    if (typeof msg !== 'string') {
      msg = typeof msg === 'object' ? JSON.stringify(msg) : String(msg)
    }
    throw new Error(status ? `[${status}] ${msg}` : (msg as string))
  }
}
