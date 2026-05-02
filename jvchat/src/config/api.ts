import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios'
import {
  getJvagentUrl,
  getJvagentTimeout,
  getConfigAsync,
  getJvforgeUrl,
} from './config'
import {
  getToken,
  getEffectiveUserId,
  getRefreshToken,
  setToken as setStorageToken,
  setRefreshToken,
  isTokenExpired,
  clearAuthSession,
} from '../utils/storage'
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
  PageIndexDocumentPatchUpdates,
  UserMemoryResponse,
  GraphExpandResponse,
  GraphSubgraphResponse,
  GoogleDriveListResponse,
  DoclingOcrEngine,
} from '../types/api'
import * as graphService from './api/graph'

class ApiClient {
  client: AxiosInstance
  private baseUrls: string[]
  /** jvforge HTTP API (``/v1/*``) — separate host/port from jvagent */
  private jvforgeBaseUrls: string[]
  private resolvedLoginPath?: string
  private isRefreshing = false
  /** Avoid multiple full-page redirects when several requests fail at once */
  private authFailureRedirectScheduled = false
  private failedQueue: Array<{
    resolve: (value?: unknown) => void
    reject: (error?: unknown) => void
  }> = []

  constructor() {
    // Initialize with default config, will be updated when config loads
    const baseURL = getJvagentUrl()
    this.baseUrls = this._buildBaseUrls(baseURL)
    this.jvforgeBaseUrls = this._buildBaseUrls(getJvforgeUrl())
    console.log('API Client initialized with baseURLs:', this.baseUrls)
    console.log('jvforge baseURLs:', this.jvforgeBaseUrls)

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
      this.jvforgeBaseUrls = this._buildBaseUrls(config.jvforge.url.replace(/\/$/, ''))
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

        if (this._isJvforgeRequest(config)) {
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
        const originalRequest = error.config as
          | (InternalAxiosRequestConfig & { _retry?: boolean })
          | undefined
        const status = error.response?.status
        const reqUrl = originalRequest?.url ?? String((error as AxiosError).config?.url ?? '')

        const isAuthEndpoint = this._isNoSessionRecoveryPath(reqUrl)
        const isHttpUnauthorized = status === 401 || status === 403
        // Browsers hide 401 bodies when error responses omit CORS headers → axios reports no `response`.
        const isLikelyCorsMaskedUnauthorized =
          !isAuthEndpoint &&
          !error.response &&
          !!originalRequest &&
          !!getToken() &&
          this._requestHadBearerAuth(originalRequest) &&
          this._axiosLooksLikeOpaqueNetworkFailure(error)

        // 401/403 (or opaque CORS-masked equiv.) on application APIs: refresh once, then logout.
        if (!isAuthEndpoint && (isHttpUnauthorized || isLikelyCorsMaskedUnauthorized)) {
          if (!originalRequest) {
            if (isHttpUnauthorized) {
              console.warn(`${status} with missing request config; redirecting to login`)
              this.redirectToLoginAfterAuthFailure()
            }
            return Promise.reject(error)
          }

          if (isLikelyCorsMaskedUnauthorized) {
            console.warn(
              'Opaque network failure on authenticated request (often CORS-blocked 401 from validate_token); attempting refresh once then logout',
              { url: error.config?.url, baseURL: error.config?.baseURL },
            )
          }

          if (!originalRequest._retry) {
            originalRequest._retry = true

            try {
              console.log(`Received ${isHttpUnauthorized ? status : 'opaque-auth failure'}, entering retry refresh flow...`)
              const token = await this._refreshAuth()
              if (token) {
                originalRequest.headers.set('Authorization', `Bearer ${token}`)
              }
              console.log('Retry refresh successful, retrying original request')
              return this.client(originalRequest)
            } catch (refreshErr) {
              // _refreshAuth already calls redirectToLoginAfterAuthFailure() before throwing.
              // Belt-and-suspenders: ensure logout even if that path is somehow bypassed.
              this.redirectToLoginAfterAuthFailure()
              return Promise.reject(refreshErr)
            }
          }

          console.warn(
            `${isHttpUnauthorized ? `${status}` : 'Opaque auth'} after token refresh (or unrecoverable auth); redirecting to login`,
          )
          this.redirectToLoginAfterAuthFailure()
          return Promise.reject(error)
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

  /** Full-page redirect: clear session and open login. Safe to call from hooks on any auth failure. */
  invalidateSessionAndRedirectToLogin(): void {
    this.redirectToLoginAfterAuthFailure()
  }

  /** True once a logout redirect has been triggered; hooks can check this to suppress stale error UI. */
  get authFailureScheduled(): boolean {
    return this.authFailureRedirectScheduled
  }

  /** Clear local session and send user to login (full navigation so routes reset). */
  private redirectToLoginAfterAuthFailure(): void {
    if (typeof window === 'undefined') return
    if (this.authFailureRedirectScheduled) return
    if (window.location.pathname === '/login') return
    this.authFailureRedirectScheduled = true
    clearAuthSession()
    window.location.replace('/login')
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
      this.redirectToLoginAfterAuthFailure()
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

  /** True when the outgoing request carried a Bearer access token (set by our request interceptor). */
  private _requestHadBearerAuth(config?: InternalAxiosRequestConfig): boolean {
    if (!config?.headers) return false
    try {
      const h = config.headers
      const v =
        typeof (h as { get?: (k: string) => unknown }).get === 'function'
          ? (h as { get: (k: string) => unknown }).get('Authorization')
          : (h as { Authorization?: unknown }).Authorization
      return typeof v === 'string' && v.startsWith('Bearer ')
    } catch {
      return false
    }
  }

  /**
   * Axios often surfaces “no response” as ERR_NETWORK / "Network Error" when the browser blocks
   * reading a 403/401 due to missing CORS on the error payload.
   */
  private _axiosLooksLikeOpaqueNetworkFailure(error: AxiosError): boolean {
    const code = (error as AxiosError & { code?: string }).code
    if (code === 'ERR_NETWORK') return true
    const m = error.message ?? ''
    return /network error/i.test(m)
  }

  /** Paths where 401 is expected (wrong password, invalid refresh) — must not trigger refresh or block host fallback. */
  private _isNoSessionRecoveryPath(url: string): boolean {
    return [
      '/auth/login',
      '/api/auth/login',
      '/auth/refresh',
      '/api/auth/refresh',
      '/auth/revoke-all',
      '/api/auth/revoke-all',
    ].some((p) => url.includes(p))
  }

  private _requestUrlFromAxiosError(error: unknown): string {
    const cfg = (error as AxiosError)?.config
    const base = cfg?.baseURL != null ? String(cfg.baseURL) : ''
    const path = cfg?.url != null ? String(cfg.url) : ''
    return `${base}${path}`
  }

  /**
   * True when this failure must not fall through to the next jvagent baseURL
   * (e.g. localhost vs 127.0.0.1), which would hide invalid/expired sessions and 401 from validate_token.
   */
  private _isSessionAuthFailureStopFallback(error: unknown): boolean {
    if (this.authFailureRedirectScheduled) return true
    const ax = error as AxiosError
    const url = this._requestUrlFromAxiosError(error)
    if (this._isNoSessionRecoveryPath(url)) return false
    const status = ax.response?.status
    if (status === 401 || status === 403) return true
    const cfg = ax.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    if (
      ax &&
      !ax.response &&
      getToken() &&
      cfg &&
      this._requestHadBearerAuth(cfg) &&
      this._axiosLooksLikeOpaqueNetworkFailure(ax)
    ) {
      return true
    }
    return false
  }

  /** True when the request targets the jvforge host (per-request baseURL), not jvagent. */
  private _isJvforgeRequest(config: InternalAxiosRequestConfig): boolean {
    const base = (config.baseURL || '').replace(/\/$/, '')
    if (!base) return false
    return this.jvforgeBaseUrls.some((u) => u.replace(/\/$/, '') === base)
  }

  private async _withFallback<T>(
    fn: (baseURL: string) => Promise<T>,
    urls?: string[],
    label?: string,
  ): Promise<T> {
    const baseUrls = urls ?? this.baseUrls
    const tag = label ?? 'Request'
    let lastError: unknown
    for (const baseURL of baseUrls) {
      try {
        return await fn(baseURL)
      } catch (error: unknown) {
        lastError = error
        if (this._isSessionAuthFailureStopFallback(error)) {
          throw error
        }
        console.warn(`${tag} failed for baseURL`, baseURL, 'error:', (error as Error)?.message || error)
      }
    }
    throw lastError
  }

  private _jvforgeHeaders(): Record<string, string> {
    const key = import.meta.env.VITE_JVFORGE_API_KEY as string | undefined
    if (key && String(key).trim() !== '') {
      return { 'X-API-Key': String(key).trim() }
    }
    return {}
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
          if (fetchResponse.status === 401 || fetchResponse.status === 403) {
            this.redirectToLoginAfterAuthFailure()
            throw new Error('Unauthorized')
          }
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
              if (fallbackResponse.status === 401 || fallbackResponse.status === 403) {
                this.redirectToLoginAfterAuthFailure()
                throw new Error('Unauthorized')
              }
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
    onError?: (error: Error) => void,
    signal?: AbortSignal
  ): Promise<void> {
    // Interact endpoint is anonymous - do not send auth headers
    // Try baseURL fallbacks and /api / non-/api paths
    const bases = this.baseUrls
    let lastError: any

    for (const base of bases) {
      for (const prefix of ['/api', '']) {
        if (signal?.aborted) {
          throw new DOMException('Aborted', 'AbortError')
        }
        const url = `${base}${prefix}/agents/${agentId}/interact`
        try {
          let response = await fetch(url, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              // No Authorization header - endpoint is anonymous
            },
            body: JSON.stringify({ ...request, stream: true }),
            signal,
          })

          if (!response.ok) {
            if (response.status === 401 || response.status === 403) {
              this.redirectToLoginAfterAuthFailure()
              throw new Error('Unauthorized')
            }
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
        catch (error: unknown) {
          // User cancelled: always propagate without retry/onError. Do not rely on
          // instanceof Error — browsers often throw DOMException for abort.
          if (signal?.aborted) {
            if (error instanceof Error) throw error
            throw new DOMException('Aborted', 'AbortError')
          }
          if (
            error instanceof Error &&
            (error.message === 'Unauthorized' || this.authFailureRedirectScheduled)
          ) {
            return
          }
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
    const finalUserId = userId || getEffectiveUserId()
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

  /**
   * Admin: delete user memory node and cascaded edges for this agent.
   * DELETE /api/agents/{agent_id}/memory/users/{user_id}
   */
  async deleteAgentMemoryUser(
    agentId: string,
    userId: string
  ): Promise<{ deleted_count?: number; message?: string }> {
    const encodedAgent = encodeURIComponent(agentId)
    const encodedUser = encodeURIComponent(userId)
    const path = `/api/agents/${encodedAgent}/memory/users/${encodedUser}`
    const fallbackPath = `/agents/${encodedAgent}/memory/users/${encodedUser}`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.delete(path, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.delete(fallbackPath, { baseURL })
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data as { deleted_count?: number; message?: string }
    return data as { deleted_count?: number; message?: string }
  }

  /**
   * Admin: purge conversations (and cascaded interactions). Uses query params only.
   * DELETE /api/agents/{agent_id}/memory/purge?conversation_id=…|user_id=…
   */
  async purgeAgentMemory(
    agentId: string,
    params: { conversation_id?: string; user_id?: string }
  ): Promise<{ purged_count?: number; message?: string }> {
    const query: Record<string, string> = {}
    if (params.conversation_id) query.conversation_id = params.conversation_id
    if (params.user_id) query.user_id = params.user_id
    const encodedAgent = encodeURIComponent(agentId)
    const path = `/api/agents/${encodedAgent}/memory/purge`
    const fallbackPath = `/agents/${encodedAgent}/memory/purge`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.delete(path, { baseURL, params: query })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.delete(fallbackPath, { baseURL, params: query })
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data as { purged_count?: number; message?: string }
    return data as { purged_count?: number; message?: string }
  }

  async getGraph(format: string = 'dot', include_attributes: boolean = true): Promise<string> {
    return graphService.getGraph({ client: this.client, _withFallback: this._withFallback.bind(this) }, format, include_attributes)
  }

  async getGraphSubgraph(params: {
    root?: string
    max_depth?: number
    max_nodes?: number
    max_edges_per_node?: number
    detail_level?: 'summary' | 'full'
  }): Promise<GraphSubgraphResponse> {
    return graphService.getGraphSubgraph({ client: this.client, _withFallback: this._withFallback.bind(this) }, params)
  }

  async getGraphExpand(params: {
    node_id: string
    direction?: string
    limit?: number
    cursor?: number
    detail_level?: 'summary' | 'full'
  }): Promise<GraphExpandResponse> {
    return graphService.getGraphExpand({ client: this.client, _withFallback: this._withFallback.bind(this) }, params)
  }

  async repairGraph(options?: {
    dry_run?: boolean
    recent_minutes?: number
    max_seconds?: number
  }): Promise<any> {
    return graphService.repairGraph({ client: this.client, _withFallback: this._withFallback.bind(this) }, options)
  }

  async getActions(
    agentId: string,
    params?: { page?: number; per_page?: number; enabled_only?: boolean }
  ): Promise<any> {
    const page = params?.page ?? 1
    const per_page = params?.per_page ?? 100
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

  async getActionByEntity(agentId: string, entity: string): Promise<any> {
    const encoded = encodeURIComponent(entity)
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(
          `/api/agents/${agentId}/actions/by-entity/${encoded}`,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(
            `/agents/${agentId}/actions/by-entity/${encoded}`,
            { baseURL }
          )
        }
        throw err
      }
    })
    return response.data
  }

  /**
   * First configured jvagent base URL with no trailing slash. Use to build absolute URLs
   * (e.g. webhooks) when the API returns a nested `{ id, entity, context }` node shape.
   */
  getJvagentBaseUrl(): string {
    return String(this.client.defaults.baseURL ?? '').replace(/\/$/, '')
  }

  /**
   * GET /api/actions/{actionId} — full action export (includes webhook_url when present).
   * Unwraps persisted node shape `{ id, entity, context }` into a flat `{ id, entity, ...context }`
   * so callers can read webhook_url and google_drive_folders at the top level.
   */
  async getAction(actionId: string): Promise<Record<string, unknown> | null> {
    const encoded = encodeURIComponent(actionId)
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(`/api/actions/${encoded}`, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(`/actions/${encoded}`, { baseURL })
        }
        throw err
      }
    })
    const data = response.data
    const inner = data?.success && data?.data ? data.data : data
    const action = inner?.action ?? inner
    if (action && typeof action === 'object' && !Array.isArray(action)) {
      const raw = action as Record<string, unknown>
      const ctx = raw.context
      if (
        ctx !== undefined &&
        ctx !== null &&
        typeof ctx === 'object' &&
        !Array.isArray(ctx)
      ) {
        const { context: _, ...rest } = raw
        return { ...rest, ...(ctx as Record<string, unknown>) }
      }
      return raw
    }
    return null
  }

  /**
   * POST PageIndex Google Drive Sync interact webhook (API key in URL query).
   * Uses fetch without JWT — matches serverless/cron triggers.
   */
  async postPageIndexGoogleDriveSyncWebhook(
    webhookUrl: string,
    body?: Record<string, unknown>
  ): Promise<unknown> {
    const res = await fetch(webhookUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'ngrok-skip-browser-warning': 'true',
      },
      body: JSON.stringify(body ?? {}),
    })
    const text = await res.text()
    let parsed: unknown
    try {
      parsed = text ? JSON.parse(text) : {}
    } catch {
      parsed = { raw: text }
    }
    if (!res.ok) {
      const detail =
        typeof parsed === 'object' &&
        parsed !== null &&
        'detail' in parsed &&
        typeof (parsed as { detail: unknown }).detail === 'string'
          ? (parsed as { detail: string }).detail
          : text || res.statusText
      throw new Error(`Webhook ${res.status}: ${detail}`)
    }
    return parsed
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
    const userId = getEffectiveUserId()
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
   * PageIndex Google Drive Sync: list folder sync state.
   * Path: GET /api/actions/{actionId}/list_google_documents
   */
  async listGoogleDriveDocuments(actionId: string): Promise<GoogleDriveListResponse> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(
          `/api/actions/${encodeURIComponent(actionId)}/list_google_documents`,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(
            `/actions/${encodeURIComponent(actionId)}/list_google_documents`,
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    const inner = data?.success && data?.data ? data.data : data
    const result = inner?.result ?? inner
    return { documents: (result?.documents ?? []) as GoogleDriveListResponse['documents'] }
  }

  /**
   * PageIndex Google Drive Sync: patch folder sync node (status, active_document, queues, metadata).
   * Path: PATCH /api/actions/{actionId}/update_google_documents
   */
  async updateGoogleDriveDocuments(
    actionId: string,
    body: {
      folder_id: string
      folder_name?: string
      metadata?: Record<string, unknown>
      status?: string
      ingesting_documents?: Record<string, unknown>
      failed_documents?: Record<string, unknown>
      active_document?: string
    }
  ): Promise<{ message?: string; result?: unknown }> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.patch(
          `/api/actions/${encodeURIComponent(actionId)}/update_google_documents`,
          body,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.patch(
            `/actions/${encodeURIComponent(actionId)}/update_google_documents`,
            body,
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * PageIndex Google Drive Sync: ingest / retry.
   * Path: POST /api/actions/{actionId}/ingest_google_documents
   */
  async ingestGoogleDocuments(
    actionId: string,
    body: {
      google_drive_folders?: { folder_id: string; metadata?: Record<string, unknown> }[]
      remove_deleted_documents?: boolean
      retry_failed_documents?: boolean
      convert_to_markdown?: boolean
      ocr?: boolean
      docling_ocr_engine?: DoclingOcrEngine
      normalize_bold_headings?: boolean
      skip_existing_documents?: boolean
    }
  ): Promise<{ message?: string; result?: unknown }> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(
          `/api/actions/${encodeURIComponent(actionId)}/ingest_google_documents`,
          body,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(
            `/actions/${encodeURIComponent(actionId)}/ingest_google_documents`,
            body,
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * PageIndex Google Drive Sync: remove folder sync node(s).
   * ``document_id`` is the Google Drive folder id when deleting one folder.
   * Path: DELETE /api/actions/{actionId}/delete_google_documents
   */
  async deleteGoogleDriveDocuments(
    actionId: string,
    body?: { document_id?: string }
  ): Promise<{ message?: string; result?: unknown }> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.delete(
          `/api/actions/${encodeURIComponent(actionId)}/delete_google_documents`,
          { baseURL, data: body ?? {} }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.delete(
            `/actions/${encodeURIComponent(actionId)}/delete_google_documents`,
            { baseURL, data: body ?? {} }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * PageIndex Google Drive Sync: per-file disable_ingestion toggle.
   * Path: POST /api/actions/{actionId}/set_google_drive_file_ingestion
   */
  async setGoogleDriveFileIngestion(
    actionId: string,
    body: { folder_id: string; file_id: string; disable_ingestion: boolean }
  ): Promise<{ message?: string; result?: unknown }> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(
          `/api/actions/${encodeURIComponent(actionId)}/set_google_drive_file_ingestion`,
          body,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(
            `/actions/${encodeURIComponent(actionId)}/set_google_drive_file_ingestion`,
            body,
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * PageIndex Google Drive Sync: prioritize or clear a file in ingest/failed queues.
   * Path: POST /api/actions/{actionId}/google_drive_file_queue
   */
  async googleDriveFileQueueOp(
    actionId: string,
    body: {
      folder_id: string
      file_id: string
      operation: 'prioritize' | 'clear'
    }
  ): Promise<{
    message?: string
    result?: {
      folder_id?: string
      file_id?: string
      prioritized_in?: 'ingesting' | 'failed' | 'enqueued'
      cleared?: boolean
    }
  }> {
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(
          `/api/actions/${encodeURIComponent(actionId)}/google_drive_file_queue`,
          body,
          { baseURL }
        )
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(
            `/actions/${encodeURIComponent(actionId)}/google_drive_file_queue`,
            body,
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
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
    * Provide either `file` or `options.fileUrl` (server downloads and ingests).
    */
  async uploadPageIndexDocument(
    agentId: string,
    file: File | null,
    options?: {
      fileUrl?: string
      docName?: string
      docDescription?: string
      docUrl?: string
      metadata?: Record<string, unknown>
      ifAddNodeSummary?: boolean
      convertToMarkdown?: boolean
      ocr?: boolean
      /** When set, sent as ``docling_ocr_engine``; overrides plain ``ocr`` on jvforge. */
      doclingOcrEngine?: DoclingOcrEngine
      normalizeBoldHeadings?: boolean
      emergency?: boolean  // NEW: Mark as emergency priority
    }
  ): Promise<PageIndexUploadResponse & {
    status?: 'queued' | 'already_queued'
    job_id?: string
    queue_position?: { overall: number, per_agent: number }
    message?: string
  }> {
    const formData = new FormData()
    const remote = options?.fileUrl?.trim()
    if (remote) {
      formData.append('file_url', remote)
    } else if (file) {
      formData.append('file', file)
    } else {
      throw new Error('uploadPageIndexDocument: provide file or options.fileUrl')
    }
    if (options?.docName) formData.append('doc_name', options.docName)
    if (options?.docDescription) formData.append('doc_description', options.docDescription)
    if (options?.docUrl) formData.append('doc_url', options.docUrl)
    if (options?.metadata) formData.append('metadata', JSON.stringify(options.metadata))
    if (options?.ifAddNodeSummary !== undefined) {
      formData.append('if_add_node_summary', options.ifAddNodeSummary ? 'yes' : 'no')
    }
    if (options?.convertToMarkdown !== undefined) {
      formData.append('convert_to_markdown', options.convertToMarkdown ? 'yes' : 'no')
    }
    if (options?.doclingOcrEngine !== undefined) {
      formData.append('docling_ocr_engine', options.doclingOcrEngine)
    }
    if (options?.ocr !== undefined) {
      formData.append('ocr', options.ocr ? 'yes' : 'no')
    }
    if (options?.normalizeBoldHeadings !== undefined) {
      formData.append('normalize_bold_headings', options.normalizeBoldHeadings ? 'yes' : 'no')
    }
    if (options?.emergency !== undefined) {
      formData.append('emergency', options.emergency ? 'true' : 'false')
    }

    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents`
    const response = await this._withFallback((baseURL) =>
      this.client.post(path, formData, {
        baseURL,
        timeout: 30000 // Shorter timeout for async mode
      })
    )
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Get jvforge processing queue for an agent (proxied through jvagent; same job shape as jvforge).
   * Path: GET /api/agents/{agentId}/pageindex/documents_queue
   */
  async getJvforgeQueue(agentId: string): Promise<{
    jobs: Array<{
      job_id: string
      doc_name: string
      status: 'queued' | 'processing' | 'completed' | 'failed' | 'webhook_failed'
      queue_position?: { overall: number, per_agent: number }
      enqueued_at: string
      agent_id?: string
      client_ref?: string
      artifact_url?: string
      error?: string
      status_url?: string
    }>
    total: number
  }> {
    type QueueJob = {
      job_id: string
      doc_name: string
      status: 'queued' | 'processing' | 'completed' | 'failed' | 'webhook_failed'
      queue_position?: { overall: number, per_agent: number }
      enqueued_at: string
      agent_id?: string
      client_ref?: string
      artifact_url?: string
      error?: string
      status_url?: string
    }
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.get(path, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.get(
            `/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue`,
            { baseURL }
          )
        }
        throw err
      }
    })
    const raw = response.data as Record<string, unknown> | unknown[] | null | undefined
    if (Array.isArray(raw)) {
      return { jobs: raw as QueueJob[], total: raw.length }
    }
    // jvagent: { success, data: { jobs, total } } — same shape as direct jvforge
    if (raw && typeof raw === 'object' && !Array.isArray(raw) && 'success' in raw) {
      const w = raw as { success?: boolean, data?: { jobs?: unknown, total?: number } }
      if (w.success && w.data && typeof w.data === 'object' && w.data !== null) {
        const d = w.data
        const jobs = Array.isArray(d.jobs) ? d.jobs : []
        const total = typeof d.total === 'number' ? d.total : jobs.length
        return { jobs: jobs as QueueJob[], total }
      }
    }
    if (raw && typeof raw === 'object' && !Array.isArray(raw) && Array.isArray((raw as { jobs?: unknown }).jobs)) {
      const o = raw as { jobs: unknown[], total?: number }
      return {
        jobs: o.jobs as QueueJob[],
        total: typeof o.total === 'number' ? o.total : o.jobs.length,
      }
    }
    return { jobs: [], total: 0 }
  }

  /**
   * Re-queue a failed jvforge job (jvagent → jvforge ``POST /v1/jobs/{jobId}/retry``).
   * Path: POST /api/agents/{agentId}/pageindex/documents_queue/{jobId}/retry
   */
  async retryPageIndexQueueJob(
    agentId: string,
    jobId: string
  ): Promise<{
    job_id: string
    status: string
    queue_position?: { overall: number, per_agent: number }
    message: string
    status_url?: string
  }> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}/retry`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(path, {}, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(
            `/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}/retry`,
            {},
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Get job queue position.
   * Path: GET /v1/jobs/{jobId}/position
   */
  async getJvforgeJobPosition(jobId: string): Promise<{
    job_id: string
    queue_position: { overall: number, per_agent: number }
    overall: number
    per_agent: number
  }> {
    const path = `/v1/jobs/${encodeURIComponent(jobId)}/position`
    const headers = this._jvforgeHeaders()
    const response = await this._withFallback(
      (baseURL) => this.client.get(path, { baseURL, headers }),
      this.jvforgeBaseUrls,
      'jvforge request',
    )
    return response.data
  }

  /**
   * Boost job to front of queue (jvagent → jvforge ``POST /v1/jobs/{jobId}/boost``).
   * Path: POST /api/agents/{agentId}/pageindex/documents_queue/{jobId}/boost
   */
  async boostPageIndexQueueJob(agentId: string, jobId: string): Promise<{
    job_id: string
    status: 'boosted'
    queue_position: { overall: number, per_agent: number }
    message: string
    status_url?: string
  }> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}/boost`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.post(path, {}, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.post(
            `/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}/boost`,
            {},
            { baseURL }
          )
        }
        throw err
      }
    })
    const data = response.data
    if (data?.success && data?.data) return data.data
    return data
  }

  /**
   * Cancel processing-queue job (jvagent → jvforge ``DELETE /v1/jobs/{jobId}``).
   * Path: DELETE /api/agents/{agentId}/pageindex/documents_queue/{jobId}
   */
  async cancelPageIndexQueueJob(agentId: string, jobId: string): Promise<{
    job_id: string
    status: 'cancelled'
    message: string
  }> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}`
    const response = await this._withFallback(async (baseURL) => {
      try {
        return await this.client.delete(path, { baseURL })
      } catch (err: any) {
        if (err.response?.status === 404) {
          return await this.client.delete(
            `/agents/${encodeURIComponent(agentId)}/pageindex/documents_queue/${encodeURIComponent(jobId)}`,
            { baseURL }
          )
        }
        throw err
      }
    })
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
    params?: { page?: number; per_page?: number; q?: string; chunk_enabled?: string }
  ): Promise<PageIndexChunksListResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/chunks`
    const query: Record<string, string | number> = {}
    if (params?.page != null) query.page = params.page
    if (params?.per_page != null) query.per_page = params.per_page
    if (params?.q != null && params.q.trim() !== '') query.q = params.q
    if (params?.chunk_enabled != null && params.chunk_enabled !== '')
      query.chunk_enabled = params.chunk_enabled
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
    params?: { page?: number; per_page?: number; q?: string; chunk_enabled?: string }
  ): Promise<PageIndexChunksListResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}/chunks`
    const query: Record<string, string | number> = {}
    if (params?.page != null) query.page = params.page
    if (params?.per_page != null) query.per_page = params.per_page
    if (params?.q != null && params.q.trim() !== '') query.q = params.q
    if (params?.chunk_enabled != null && params.chunk_enabled !== '')
      query.chunk_enabled = params.chunk_enabled
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
   * Update document root metadata and/or source URL (applies to all chunks).
   * Path: PATCH /api/agents/{agentId}/pageindex/documents/{docName}
   */
  async patchPageIndexDocumentMetadata(
    agentId: string,
    docName: string,
    partial: PageIndexDocumentPatchUpdates
  ): Promise<PageIndexDocumentMetadataResponse> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/documents/${encodeURIComponent(docName)}`
    const updates: Record<string, unknown> = {}
    if ('metadata' in partial) updates.metadata = partial.metadata
    if ('doc_url' in partial) updates.doc_url = partial.doc_url
    const response = await this._withFallback((baseURL) =>
      this.client.patch(path, { updates }, { baseURL })
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
    if (params.include_references !== undefined) body.include_references = params.include_references

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
  async exportPageIndex(
    format: 'json' = 'json',
    collectionName: string = 'default',
    rootId?: string
  ): Promise<any> {
    const path = `/api/agents/${encodeURIComponent(collectionName)}/pageindex/export`
    const params: Record<string, string> = { format }
    if (rootId) params.root_id = rootId
    const response = await this._withFallback((baseURL) =>
      this.client.get(path, { baseURL, params })
    )
    return response.data.data
  }

  /**
   * Import PageIndex data.
   * Path: POST /api/agents/{agentId}/pageindex/import
   * Provide either `data` or `importUrl` (not both).
   */
  async importPageIndex(
    agentId: string,
    params: { data?: any; importUrl?: string; purge?: boolean }
  ): Promise<any> {
    const path = `/api/agents/${encodeURIComponent(agentId)}/pageindex/import`
    const purge = params.purge ?? false
    const url = params.importUrl?.trim()
    const body: Record<string, unknown> = { purge }
    if (url) {
      body.import_url = url
    } else if (params.data !== undefined) {
      body.data = params.data
    } else {
      throw new Error('importPageIndex: provide data or importUrl')
    }
    const response = await this._withFallback((baseURL) =>
      this.client.post(path, body, { baseURL })
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
