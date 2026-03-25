import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios'
import { getJvagentUrl, getJvagentTimeout, getConfigAsync } from './config'
import { getToken, removeToken, getUserId, getRefreshToken, setToken as setStorageToken, setRefreshToken, removeRefreshToken, isTokenExpired } from '../utils/storage'
import type {
  LoginRequest,
  LoginResponse,
  TokenRefreshRequest,
  TokenRefreshResponse,
  AgentsResponse,
  InteractionRequest,
  InteractionResponse,
  LogsResponse,
  PageIndexListResponse,
  PageIndexUploadResponse,
  PageIndexDeleteResponse,
  PageIndexSearchResponse,
  PageIndexSearchParams,
  PageIndexChunksListResponse,
  PageIndexChunkDetailResponse,
  PageIndexChunkUpdatePayload,
  PageIndexChunkDeleteResponse,
  PageIndexDocumentMetadataResponse,
  UserMemoryResponse,
} from '../types/api'

class ApiClient {
  private client: AxiosInstance
  private baseUrls: string[]
  private resolvedLoginPath?: string
  private isRefreshing = false
  private failedQueue: Array<{
    resolve: (value?: unknown) => void
    reject: (error?: unknown) => void
  }> = []

  constructor() {
    // Initialize with default config, will be updated when config loads
    const baseURL = getJvagentUrl()
    this.baseUrls = this._buildBaseUrls(baseURL)
    console.log('API Client initialized with baseURLs:', this.baseUrls)

    this.client = axios.create({
      baseURL: baseURL,
      timeout: getJvagentTimeout(),
      // Remove default Content-Type header to avoid preflights on GET requests
      // Headers will be set per-request in the interceptor
      headers: {},
      // Enable cookies for session-based auth; harmless for bearer-token flows.
      withCredentials: true,
    })

    // Update baseURL when async config loads
    getConfigAsync().then((config) => {
      if (config.jvagent.url !== baseURL) {
        this.updateBaseUrl(config.jvagent.url)
      }
    }).catch((err) => {
      console.warn('Failed to load async config:', err)
    })

    // Request interceptor to add JWT token and handle proactive refresh
    this.client.interceptors.request.use(
      async (config) => {
        // Set Content-Type for state-changing requests if not set (skip for FormData - axios sets multipart boundary)
        if (['post', 'put', 'patch', 'delete'].includes(config.method?.toLowerCase() || '')) {
          if (!config.headers.get('Content-Type') && !(config.data instanceof FormData)) {
            config.headers.set('Content-Type', 'application/json')
          }
        }

        // Skip Authorization header for auth-related and anonymous paths
        const authPaths = ['/auth/login', '/api/auth/login', '/auth/refresh', '/api/auth/refresh']
        const isAuth = authPaths.some(path => config.url?.includes(path))

        // Strictly skip anonymous interact endpoint: /api/agents/{id}/interact
        const isAnonymousInteract = config.url && (
          config.url.endsWith('/interact') ||
          config.url.includes('/interact?') ||
          /\/agents\/[^/]+\/interact($|\?)/.test(config.url)
        )

        if (isAuth || isAnonymousInteract) {
          console.log('Skipping Authorization header for:', config.url)
          return config
        }

        let token = getToken()

        // Proactive token refresh if expired or about to expire
        if (token && isTokenExpired(token)) {
          console.log('Token expired or expiring soon, triggering proactive refresh for:', config.url)
          try {
            const newToken = await this._refreshAuth()
            token = newToken
          } catch (err) {
            console.error('Proactive refresh failed:', err)
            // Error handling is inside _refreshAuth (redirect to login if both fail)
            return Promise.reject(err)
          }
        }

        if (token) {
          config.headers.set('Authorization', `Bearer ${token}`)
        } else {
          console.warn('Request without token:', {
            url: config.url,
            method: config.method
          })
        }
        return config
      },
      (error) => Promise.reject(error)
    )

    // Response interceptor for error handling and token refresh (standard retry)
    this.client.interceptors.response.use(
      (response) => response,
      async (error: AxiosError) => {
        const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

        // Handle 401 errors with token refresh (fallback if proactive check missed it)
        if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
          originalRequest._retry = true

          try {
            console.log('Received 401, entering retry refresh flow...')
            const token = await this._refreshAuth()
            if (token) {
              originalRequest.headers.set('Authorization', `Bearer ${token}`)
            }
            console.log('Retry refresh successful, retrying original request')
            return this.client(originalRequest)
          } catch (refreshErr) {
            return Promise.reject(refreshErr)
          }
        }

        // Check for "Network Error" which might be a CORS-blocked 401
        // If we have a token and it's a network error, it's highly suspicious
        if (!error.response && getToken()) {
          console.warn('Network Error detected with an existing token. This might be a CORS-blocked 401.', {
            url: error.config?.url,
            baseURL: error.config?.baseURL
          })
          // We don't automatically retry here to avoid loops,
          // but the proactive check in the request interceptor should prevent this mostly.
        }

        console.error('API Error:', {
          message: error.message,
          status: error.response?.status,
          statusText: error.response?.statusText,
          data: error.response?.data,
          url: error.config?.url,
          baseURL: error.config?.baseURL,
        })

        // Network errors (no response)
        if (!error.response) {
          console.error('Network Error - No response from server. Possible causes:')
          console.error('1. Server is not running')
          console.error('2. CORS is blocking the request (often due to 401 response missing CORS headers)')
          console.error('3. Wrong URL:', this.client.defaults.baseURL)
          console.error('4. Network connectivity issue')
        }

        return Promise.reject(error)
      }
    )
  }

  /**
   * Internal flow to refresh auth tokens either via refresh token or auto-login.
   * Consolidates logic to avoid duplication in interceptors.
   * Returns a promise that resolves to the new access token.
   */
  private async _refreshAuth(): Promise<string> {
    // If already refreshing, wait for it to complete
    if (this.isRefreshing) {
      return new Promise((resolve, reject) => {
        this.failedQueue.push({
          resolve: () => resolve(getToken()!),
          reject
        })
      })
    }

    this.isRefreshing = true

    const cleanUpAndRedirect = (error: any) => {
      this.failedQueue.forEach(({ reject }) => reject(error))
      this.failedQueue = []
      this.isRefreshing = false
      removeToken()
      removeRefreshToken()
      if (window.location.pathname !== '/login') {
        window.location.replace('/login')
      }
      throw error
    }

    try {
      const refreshToken = getRefreshToken()
      if (refreshToken) {
        try {
          console.log('Attempting token refresh...')
          const refreshResponse = await this.refreshToken({ refresh_token: refreshToken })
          setStorageToken(refreshResponse.access_token)
          if (refreshResponse.refresh_token) {
            setRefreshToken(refreshResponse.refresh_token)
          }
          console.log('Token refresh successful')

          this.failedQueue.forEach(({ resolve }) => resolve())
          this.failedQueue = []
          this.isRefreshing = false
          return refreshResponse.access_token
        } catch (refreshErr) {
          console.warn('Refresh token failed')
        }
      }

      console.warn('No refresh token available for recovery')
      return cleanUpAndRedirect(new Error('Authentication expired and no recovery credentials found'))
    } catch (err) {
      return cleanUpAndRedirect(err)
    }
  }

  /**
   * Manually set the access token.
   * Useful for explicit auth flows in components.
   */
  setToken(token: string | LoginResponse): void {
    if (typeof token === 'string') {
      setStorageToken(token)
    } else if (token && token.access_token) {
      setStorageToken(token.access_token)
      if (token.refresh_token) {
        setRefreshToken(token.refresh_token)
      }
    }
  }

  /**
   * Manually set the refresh token.
   */
  setRefreshToken(token: string): void {
    setRefreshToken(token)
  }

  /**
   * Update the base URL for the API client.
   * This is called when the user changes the server URL in the login form.
   */
  updateBaseUrl(url: string): void {
    this.baseUrls = this._buildBaseUrls(url)
    console.log('Updating API client baseURLs to:', this.baseUrls)
    this.client.defaults.baseURL = this.baseUrls[0]
    // Update timeout from config
    this.client.defaults.timeout = getJvagentTimeout()
    // Reset resolved login path when URL changes
    this.resolvedLoginPath = undefined
  }

  private _buildBaseUrls(primary: string): string[] {
    const urls = [primary]
    const swapped = this._swapHost(primary)
    if (swapped && swapped !== primary) {
      urls.push(swapped)
    }
    return urls
  }

  private _swapHost(url: string): string | null {
    if (url.includes('localhost')) {
      return url.replace('localhost', '127.0.0.1')
    }
    if (url.includes('127.0.0.1')) {
      return url.replace('127.0.0.1', 'localhost')
    }
    return null
  }

  private async _withFallback<T>(fn: (baseURL: string) => Promise<T>): Promise<T> {
    let lastError: unknown
    for (const baseURL of this.baseUrls) {
      try {
        return await fn(baseURL)
      } catch (error: unknown) {
        lastError = error
        console.warn('Request failed for baseURL', baseURL, 'error:', (error as Error)?.message || error)
        // Try next baseURL
      }
    }
    throw lastError
  }

  async login(credentials: LoginRequest): Promise<LoginResponse> {
    try {
      // If serverUrl is provided, update the base URL first
      if (credentials.serverUrl) {
        this.updateBaseUrl(credentials.serverUrl)
      }

      // Extract login credentials (without serverUrl)
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { serverUrl, ...loginCreds } = credentials

      // If we already found a working login path, use it directly (single call)
      if (this.resolvedLoginPath) {
        const response = await this._withFallback((baseURL) =>
          this.client.post(this.resolvedLoginPath!, loginCreds, { baseURL })
        )
        return this._extractLoginResponse(response)
      }

      // Otherwise, try path + base fallbacks and remember the first successful path
      const loginPaths = ['/api/auth/login', '/auth/login']
      let lastError: any

      for (const baseURL of this.baseUrls) {
        for (const path of loginPaths) {
          try {
            const response = await this.client.post(path, loginCreds, { baseURL })
            this.resolvedLoginPath = path
            return this._extractLoginResponse(response)
          } catch (err: unknown) {
            lastError = err
            if ((err as AxiosError)?.response?.status === 404) {
              // try next path/base
              continue
            }
            // non-404 -> stop trying
            throw err
          }
        }
      }

      throw lastError || new Error('Login failed')
    } catch (error: any) {
      console.error('Login error:', error)
      console.error('Error response:', error.response?.data)
      throw error
    }
  }

  private _extractLoginResponse(response: any): LoginResponse {
    const payload = response?.data ?? response
    if (!payload) {
      throw new Error('Empty login response')
    }

    // Handle wrapped success_response format
    if (payload.success && payload.data) {
      return payload.data as LoginResponse
    }

    // Handle direct TokenResponse (includes refresh_token and refresh_expires_in)
    if (payload.access_token && payload.token_type) {
      return payload as LoginResponse
    }

    throw new Error(
      payload.detail || payload.message || 'Unexpected login response format'
    )
  }

  async refreshToken(request: TokenRefreshRequest): Promise<TokenRefreshResponse> {
    try {
      // Try /api/auth/refresh first, fallback to /auth/refresh
      const refreshPaths = ['/api/auth/refresh', '/auth/refresh']
      let lastError: any

      for (const baseURL of this.baseUrls) {
        for (const path of refreshPaths) {
          try {
            // Don't use axios client here to avoid interceptor loops
            const response = await fetch(`${baseURL}${path}`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify(request),
            })

            if (!response.ok) {
              if (response.status === 404) {
                continue // Try next path
              }
              const errorText = await response.text()
              let errorMessage = `HTTP error! status: ${response.status}`
              try {
                const errorJson = JSON.parse(errorText)
                errorMessage = errorJson.detail || errorJson.message || errorMessage
              } catch {
                errorMessage = errorText || errorMessage
              }
              throw new Error(errorMessage)
            }

            const data = await response.json()
            return this._extractLoginResponse({ data })
          } catch (err: any) {
            lastError = err
            if (err.message?.includes('404') || err.message?.includes('HTTP error! status: 404')) {
              continue // Try next path
            }
            // Non-404 error, stop trying
            throw err
          }
        }
      }

      throw lastError || new Error('Token refresh failed')
    } catch (error: any) {
      console.error('Token refresh error:', error)
      throw error
    }
  }

  async logout(): Promise<void> {
    try {
      // Try /api/auth/revoke-all first, fallback to /auth/revoke-all
      const revokePaths = ['/api/auth/revoke-all', '/auth/revoke-all']
      let lastError: any
      let success = false

      for (const baseURL of this.baseUrls) {
        for (const path of revokePaths) {
          try {
            await this.client.post(path, {}, { baseURL })
            success = true
            console.log('Successfully revoked all tokens on server')
            break
          } catch (err: any) {
            lastError = err
            if (err.response?.status === 404) {
              continue // Try next path
            }
            // For 401, token might already be invalid - that's okay, we're logging out anyway
            if (err.response?.status === 401) {
              console.warn('Logout: Token already invalid or expired, continuing with local logout')
              success = true // Consider this success since we're logging out anyway
              break
            }
            // For other errors, try next path/base
            continue
          }
        }
        if (success) break
      }

      if (!success && lastError) {
        // Log error but don't throw - we still want to clear local storage
        console.warn('Logout: Failed to revoke tokens on server:', lastError.message || lastError)
      }
    } catch (error: any) {
      // Log error but don't throw - always allow local logout
      console.warn('Logout: Error during server logout, continuing with local logout:', error)
    }
  }

  async getAgents(enabled?: boolean): Promise<AgentsResponse> {
    const params = enabled !== undefined ? { enabled } : {}
    try {
      // Try /api/agents first (default jvspatial prefix), fallback to /agents, with baseURL fallbacks
      const response = await this._withFallback(async (baseURL) => {
        try {
          return await this.client.get<any>('/api/agents', { params, baseURL })
        } catch (err: any) {
          if (err.response?.status === 404) {
            return await this.client.get<any>('/agents', { params, baseURL })
          }
          throw err
        }
      })
      console.log('Agents API response:', response.data)
      // Handle different response structures
      const data = response.data

      // Case 1: { success: true, agents: [...], ... } - direct structure
      if (data && data.success && data.agents) {
        return data as AgentsResponse
      }

      // Case 2: { success: true, data: { agents: [...], ... } } - nested structure
      if (data && data.success && data.data && data.data.agents) {
        return data.data as AgentsResponse
      }

      // Case 3: { agents: [...], ... } - unwrapped structure
      if (data && data.agents) {
        return data as AgentsResponse
      }

      // Fallback: return as-is
      return data as AgentsResponse
    } catch (error: any) {
      console.error('Error in getAgents:', error)
      console.error('Response:', error.response?.data)
      console.error('URL attempted:', error.config?.url)
      throw error
    }
  }

  async interact(
    agentId: string,
    request: InteractionRequest
  ): Promise<InteractionResponse> {
    // Interact endpoint is anonymous - create a request without auth headers
    // Try /api/agents/{id}/interact first, fallback to /agents/{id}/interact, with baseURL fallbacks
    const response = await this._withFallback(async (baseURL) => {
      try {
        // Use fetch directly to avoid axios interceptor adding auth headers
        const url = `${baseURL}/api/agents/${agentId}/interact`
        const fetchResponse = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(request),
        })

        if (!fetchResponse.ok) {
          if (fetchResponse.status === 404) {
            // Try without /api prefix
            const fallbackUrl = `${baseURL}/agents/${agentId}/interact`
            const fallbackResponse = await fetch(fallbackUrl, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify(request),
            })

            if (!fallbackResponse.ok) {
              const errorText = await fallbackResponse.text()
              let errorMessage = `HTTP error! status: ${fallbackResponse.status}`
              try {
                const errorJson = JSON.parse(errorText)
                errorMessage = errorJson.detail || errorJson.message || errorMessage
              } catch {
                errorMessage = errorText || errorMessage
              }
              throw new Error(errorMessage)
            }

            return { data: await fallbackResponse.json() }
          }

          const errorText = await fetchResponse.text()
          let errorMessage = `HTTP error! status: ${fetchResponse.status}`
          try {
            const errorJson = JSON.parse(errorText)
            errorMessage = errorJson.detail || errorJson.message || errorMessage
          } catch {
            errorMessage = errorText || errorMessage
          }
          throw new Error(errorMessage)
        }

        return { data: await fetchResponse.json() }
      } catch (err: any) {
        throw err
      }
    })
    // Handle both wrapped (success_response) and unwrapped responses
    if (response.data.success && response.data.data) {
      return response.data.data as InteractionResponse
    }
    return response.data as InteractionResponse
  }

  async streamInteract(
    agentId: string,
    request: InteractionRequest,
    onChunk: (chunk: any) => void,
    onError?: (error: Error) => void
  ): Promise<void> {
    // Interact endpoint is anonymous - do not send auth headers
    // Try baseURL fallbacks and /api / non-/api paths
    const bases = this.baseUrls
    let lastError: any

    for (const base of bases) {
      for (const prefix of ['/api', '']) {
        const url = `${base}${prefix}/agents/${agentId}/interact`
        try {
          let response = await fetch(url, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              // No Authorization header - endpoint is anonymous
            },
            body: JSON.stringify({ ...request, stream: true }),
          })

          if (!response.ok) {
            if (response.status === 404 && prefix === '/api') {
              // try next prefix in same base
              continue
            }
            const errorText = await response.text()
            let errorMessage = `HTTP error! status: ${response.status}`
            try {
              const errorJson = JSON.parse(errorText)
              errorMessage = errorJson.detail || errorJson.message || errorMessage
            } catch {
              errorMessage = errorText || errorMessage
            }
            throw new Error(errorMessage)
          }

          const reader = response.body?.getReader()
          if (!reader) {
            throw new Error('Response body is not readable')
          }

          const decoder = new TextDecoder()
          let buffer = ''

          while (true) {
            const { done, value } = await reader.read()
            if (done) break

            buffer += decoder.decode(value, { stream: true })
            const chunks = buffer.split('\n\n')
            buffer = chunks.pop() || ''

            for (const chunk of chunks) {
              if (chunk.trim()) {
                const lines = chunk.split('\n')
                let data: any = null

                for (const line of lines) {
                  if (line.startsWith('data:')) {
                    const dataStr = line.substring(5).trim()
                    try {
                      data = JSON.parse(dataStr)
                      onChunk(data)
                    } catch (e) {
                      console.error('Failed to parse SSE chunk:', e)
                    }
                  }
                }
              }
            }
          }

          // Successful stream; exit both loops
          return
        }
        catch (error) {
          lastError = error
          const errorMessage =
            error instanceof Error
              ? error.message
              : 'An unexpected error occurred while streaming'
          // Try next prefix/base; only surface after all options exhausted
          if (onError && prefix === '' && base === bases[bases.length - 1]) {
            onError(new Error(errorMessage))
          }
        }
      }
    }

    // If we reach here, all attempts failed
    if (lastError) {
      throw lastError
    }
  }

  async deleteConversation(agentId: string, userId: string, sessionId: string): Promise<void> {
    // Get user_id from localStorage if not provided
    const finalUserId = userId || getUserId()
    if (!finalUserId) {
      throw new Error('User ID is required to delete conversation')
    }

    // Use the correct endpoint with user_id as a path parameter
    // Path structure: /api/agents/{agent_id}/conversations/{user_id}/{session_id}
    const endpoint = `/api/agents/${agentId}/conversations/${encodeURIComponent(finalUserId)}/${encodeURIComponent(sessionId)}`

    try {
      await this._withFallback(async (baseURL) => {
        await this.client.delete(endpoint, { baseURL })
      })
    } catch (error: any) {
      // Handle 404 as non-critical (conversation might not exist on server)
      if (error.response?.status === 404) {
        console.warn(`Conversation ${sessionId} not found on server (may have been deleted already)`)
        return
      }
      // Re-throw other errors
      throw error
    }
  }

  async getGraph(format: string = 'dot', include_attributes: boolean = true): Promise<string> {
    // Endpoint returns plain text (DOT or Mermaid diagram syntax)
    // Try /api/graph first, fallback to /graph, with baseURL fallbacks
    try {
      const params = {
        format,
        include_attributes: include_attributes,
      }

      const response = await this._withFallback(async (baseURL) => {
        try {
          // Use responseType: 'text' to get plain text response
          return await this.client.get('/api/graph', {
            params,
            baseURL,
            responseType: 'text',
          })
        } catch (err: any) {
          if (err.response?.status === 404) {
            // Try without /api prefix
            return await this.client.get('/graph', {
              params,
              baseURL,
              responseType: 'text',
            })
          }
          throw err
        }
      })

      // Response data is already a string when responseType is 'text'
      return response.data as string
    } catch (error: any) {
      console.error('Error fetching graph:', error)
      const errorMessage =
        error.response?.data ||
        error.message ||
        'Failed to fetch graph data'
      throw new Error(
        typeof errorMessage === 'string' ? errorMessage : 'Failed to fetch graph data'
      )
    }
  }

  async repairGraph(options?: { dry_run?: boolean; recent_minutes?: number }): Promise<any> {
    try {
      const params: Record<string, string | number | boolean> = {}
      if (options?.dry_run !== undefined) params.dry_run = options.dry_run
      if (options?.recent_minutes !== undefined) params.recent_minutes = options.recent_minutes

      const response = await this._withFallback(async (baseURL) => {
        try {
          return await this.client.post('/api/graph/repair', {}, { params, baseURL })
        } catch (err: any) {
          if (err.response?.status === 404) {
            return await this.client.post('/graph/repair', {}, { params, baseURL })
          }
          throw err
        }
      })

      const data = response.data
      if (data?.success && data?.data) return data.data
      return data
    } catch (error: any) {
      console.error('Error repairing graph:', error)
      const status = error.response?.status
      let errorMessage: string | unknown = error.response?.data?.detail ?? error.response?.data?.message ?? error.message ?? 'Failed to repair graph'
      if (Array.isArray(errorMessage)) {
        errorMessage = errorMessage.map((e: { msg?: string; message?: string }) => e?.msg ?? e?.message ?? String(e)).join('; ')
      }
      if (typeof errorMessage !== 'string') {
        errorMessage = typeof errorMessage === 'object' ? JSON.stringify(errorMessage) : String(errorMessage)
      }
      const prefix = status ? `[${status}] ` : ''
      throw new Error(prefix + errorMessage)
    }
  }

  async getActions(
    agentId: string,
    params?: { page?: number; per_page?: number; enabled_only?: boolean }
  ): Promise<any> {
    const page = params?.page ?? 1
    const per_page = params?.per_page ?? 30
    const enabled_only = params?.enabled_only ?? false
    const query = `page=${page}&per_page=${per_page}&enabled_only=${enabled_only}`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(`/api/agents/${agentId}/actions?${query}`, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(`/agents/${agentId}/actions?${query}`, { baseURL })
        }
        throw err
      }
    })
    return response.data
  }

  /**
   * Update an action. Only pass fields you want to update.
   * Path: PUT /api/actions/{actionId}
   */
  async updateAction(
    actionId: string,
    payload: { description?: string; enabled?: boolean; properties?: Record<string, unknown> }
  ): Promise<any> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.put(`/api/actions/${actionId}`, payload, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.put(`/actions/${actionId}`, payload, { baseURL })
        }
        throw err
      }
    })
    return response.data
  }

  async getMyMemory(agentId: string): Promise<UserMemoryResponse> {
    const userId = getUserId()
    const url = `/api/agents/${agentId}/memory/me${userId ? `?user_id=${encodeURIComponent(userId)}` : ''}`
    const fallbackUrl = `/agents/${agentId}/memory/me${userId ? `?user_id=${encodeURIComponent(userId)}` : ''}`

    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(url, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(fallbackUrl, { baseURL })
        }
        throw err
      }
    })

    const data = response.data
    // Handle success_response wrapper
    if (data && data.success && data.data) {
      return data.data as UserMemoryResponse
    }
    return data as UserMemoryResponse
  }

  /**
   * Reload an action after update.
   * Path: POST /api/actions/{actionId}/reload
   */
  async reloadAction(actionId: string): Promise<any> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(`/api/actions/${actionId}/reload`, {}, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(`/actions/${actionId}/reload`, {}, { baseURL })
        }
        throw err
      }
    })
    return response.data
  }

  /**
   * List documents in the agent's PageIndex collection.
   * Path: GET /api/agents/{agentId}/pageindex/documents
   */
  async listPageIndexDocuments(
    agentId: string,
    params?: { metadata?: Record<string, unknown> }
  ): Promise<PageIndexListResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents`
    const queryParams = params?.metadata
      ? { metadata: JSON.stringify(params.metadata) }
      : undefined
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL, params: queryParams })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Upload a document to the agent's PageIndex collection.
   * Path: POST /api/agents/{agentId}/pageindex/documents
   */
  async uploadPageIndexDocument(
    agentId: string,
    file: File,
    options?: {
      docName?: string
      docDescription?: string
      docUrl?: string
      metadata?: Record<string, unknown>
      ifAddNodeSummary?: boolean
    }
  ): Promise<PageIndexUploadResponse> {
    const formData = new FormData()
    formData.append('file', file)
    if (options?.docName) formData.append('doc_name', options.docName)
    if (options?.docDescription) formData.append('doc_description', options.docDescription)
    if (options?.docUrl) formData.append('doc_url', options.docUrl)
    if (options?.metadata) formData.append('metadata', JSON.stringify(options.metadata))
    if (options?.ifAddNodeSummary !== undefined) {
      formData.append('if_add_node_summary', options.ifAddNodeSummary ? 'yes' : 'no')
    }

    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents`
    const response = await this._withFallback((baseURL) =>
      this.client.post(path, formData, {
        baseURL,
        timeout: 1000000 // 5 minutes for file uploads
      })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Delete a document from the agent's PageIndex collection.
   * Path: DELETE /api/agents/{agentId}/pageindex/documents/{docName}
   */
  async deletePageIndexDocument(agentId: string, docName: string): Promise<PageIndexDeleteResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}`
    const response = await this._withFallback((baseURL) =>
      this.client.delete(path, { baseURL })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * List chunks across all documents in the agent PageIndex collection.
   * Path: GET /api/agents/{agentId}/pageindex/chunks
   */
  async listPageIndexChunksForCollection(
    agentId: string,
    params?: { page?: number; per_page?: number; q?: string }
  ): Promise<PageIndexChunksListResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/chunks`
    const query: Record<string, string | number> = {}
    if (params?.page != null) query.page = params.page
    if (params?.per_page != null) query.per_page = params.per_page
    if (params?.q != null && params.q.trim() !== '') query.q = params.q
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL, params: query })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * List chunks for a PageIndex document.
   * Path: GET /api/agents/{agentId}/pageindex/documents/{docName}/chunks
   */
  async listPageIndexChunks(
    agentId: string,
    docName: string,
    params?: { page?: number; per_page?: number; q?: string }
  ): Promise<PageIndexChunksListResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}/chunks`
    const query: Record<string, string | number> = {}
    if (params?.page != null) query.page = params.page
    if (params?.per_page != null) query.per_page = params.per_page
    if (params?.q != null && params.q.trim() !== '') query.q = params.q
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL, params: query })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Get a single PageIndex chunk by graph node id.
   */
  async getPageIndexChunk(
    agentId: string,
    docName: string,
    chunkId: string
  ): Promise<PageIndexChunkDetailResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}/chunks/${encodeURIComponent(chunkId)}`
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Update PageIndex chunk fields.
   */
  async updatePageIndexChunk(
    agentId: string,
    docName: string,
    chunkId: string,
    payload: PageIndexChunkUpdatePayload
  ): Promise<PageIndexChunkDetailResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}/chunks/${encodeURIComponent(chunkId)}`
    const response = await this._withFallback((baseURL) =>
      this.client.patch(path, { updates: payload }, { baseURL })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Delete a PageIndex chunk (subtree when cascade is true).
   */
  async deletePageIndexChunk(
    agentId: string,
    docName: string,
    chunkId: string,
    options?: { cascade?: boolean }
  ): Promise<PageIndexChunkDeleteResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}/chunks/${encodeURIComponent(chunkId)}`
    const params =
      options?.cascade === false ? { cascade: false } : undefined
    const response = await this._withFallback((baseURL) =>
      this.client.delete(path, { baseURL, params })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Update document root metadata (applies to all chunks in that document).
   * Path: PATCH /api/agents/{agentId}/pageindex/documents/{docName}
   */
  async patchPageIndexDocumentMetadata(
    agentId: string,
    docName: string,
    metadata: Record<string, unknown> | null
  ): Promise<PageIndexDocumentMetadataResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}`
    const response = await this._withFallback((baseURL) =>
      this.client.patch(path, { updates: { metadata } }, { baseURL })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Search documents in the agent's PageIndex collection.
   * Path: POST /api/agents/{agentId}/pageindex/documents/search
   */
  async searchPageIndexDocuments(
    agentId: string,
    params: PageIndexSearchParams
  ): Promise<PageIndexSearchResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/search`
    const body: Record<string, unknown> = {
      query: params.query,
      strategy: params.strategy ?? 'tree_search',
      limit: params.limit ?? 10,
    }
    if (params.doc_name != null) body.doc_name = params.doc_name
    if (params.metadata != null) body.metadata = JSON.stringify(params.metadata)

    const response = await this._withFallback((baseURL) =>
      this.client.post(path, body, { baseURL })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Export PageIndex data.
   * Path: GET /api/agents/{agentId}/pageindex/export
   */
  async exportPageIndex(format: 'json' = 'json', collectionName: string = 'default'): Promise<any> {
    const path = `/api/agents/${encodeURIComponent(collectionName)}/pageindex/export`
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL, params: { format } })
    )
    return response.data.data
  }

  /**
   * Import PageIndex data.
   * Path: POST /api/agents/{agentId}/pageindex/import
   */
  async importPageIndex(agentId: string, data: any, purge: boolean = false): Promise<any> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/import`
    const response = await this._withFallback((baseURL) =>
      this.client.post(path, { purge, data }, { baseURL })
    )
    const responseData = response.data
    if (responseData?.success && responseData?.data) return responseData.data
    return responseData
  }

  async getInteractions(actionId: string): Promise<any> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(`/api/actions/${actionId}/interactions`, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(`/actions/${actionId}/interactions`, { baseURL })
        }
        throw err
      }
    })
    return response.data
  }

  async getUsers(
    agentId: string,
    userIds: string[],
    options?: { filter?: string; page?: number; page_size?: number }
  ): Promise<Record<string, string>> {
    if (!agentId) return {}
    const params: Record<string, string | number> = {
      page: options?.page ?? 1,
      page_size: options?.page_size ?? 200,
    }
    if (options?.filter) {
      params.filter = options.filter
    } else if (userIds.length) {
      params.filter = JSON.stringify({ 'context.user_id': { $in: userIds } })
    }
    const path = `/api/agents/${agentId}/memory/users`
    try {
      const response = await this._withFallback(async (baseURL) => {
        try {
          return await this.client.get(path, { baseURL, params })
        } catch (err: any) {
          if (err.response?.status === 404) {
            return await this.client.get(`/agents/${agentId}/memory/users`, {
              baseURL,
              params,
            })
          }
          throw err
        }
      })
      const data = response.data
      const users = data?.users ?? data?.data?.users ?? []
      if (!Array.isArray(users)) return {}
      const nameMap: Record<string, string> = {}
      for (const u of users) {
        const ctx = u?.context ?? {}
        const uid = ctx.user_id
        const name = ctx.display_name ?? ctx.name
        if (uid && name && typeof name === 'string') nameMap[uid] = name.trim()
      }
      return nameMap
    } catch {
      return {}
    }
  }

  async getUsersPaginated(
    agentId: string,
    params: { filter?: string; page?: number; page_size?: number } = {}
  ): Promise<{ users: any[]; pagination: { page: number; page_size: number; total: number; total_pages: number } }> {
    if (!agentId) return { users: [], pagination: { page: 1, page_size: 50, total: 0, total_pages: 0 } }
    const queryParams: Record<string, string | number> = {
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    }
    if (params.filter) queryParams.filter = params.filter
    const path = `/api/agents/${agentId}/memory/users`
    try {
      const response = await this._withFallback(async (baseURL) => {
        try {
          return await this.client.get(path, { baseURL, params: queryParams })
        } catch (err: any) {
          if (err.response?.status === 404) {
            return await this.client.get(`/agents/${agentId}/memory/users`, {
              baseURL,
              params: queryParams,
            })
          }
          throw err
        }
      })
      const data = response.data
      const users = data?.users ?? data?.data?.users ?? []
      const pagination = data?.pagination ?? data?.data?.pagination ?? {}
      return {
        users: Array.isArray(users) ? users : [],
        pagination: {
          page: pagination.page ?? 1,
          page_size: pagination.page_size ?? 50,
          total: pagination.total ?? 0,
          total_pages: pagination.total_pages ?? 0,
        },
      }
    } catch {
      return { users: [], pagination: { page: 1, page_size: 50, total: 0, total_pages: 0 } }
    }
  }

  async getLogs(params: {
    category?: string
    agent_id?: string
    user_id?: string
    filter?: string
    page?: number
    page_size?: number
  }): Promise<LogsResponse> {
    const { category, agent_id, user_id, filter, page = 1, page_size = 50 } =
      params
    const queryParams: Record<string, string | number> = { page, page_size }
    if (category) queryParams.category = category
    if (filter) {
      queryParams.filter = filter
    } else if (agent_id || user_id) {
      const filterObj: Record<string, string> = {}
      if (agent_id) filterObj["context.log_data.agent_id"] = agent_id
      if (user_id) filterObj["context.log_data.user_id"] = user_id
      queryParams.filter = JSON.stringify(filterObj)
    }

    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get('/api/logs', { baseURL, params: queryParams })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get('/logs', { baseURL, params: queryParams })
        }
        throw err
      }
    })

    const data = response.data
    if (data?.success && data?.data) {
      return data.data as LogsResponse
    }
    return data as LogsResponse
  }

  async queryAction(actionId: string, payload: any): Promise<any> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(`/api/actions/${actionId}/query`, payload, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(`/actions/${actionId}/query`, payload, { baseURL })
        }
        throw err
      }
    })
    return response.data
  }
}

export const apiClient = new ApiClient()
