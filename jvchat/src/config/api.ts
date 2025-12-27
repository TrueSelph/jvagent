import axios, { AxiosInstance, AxiosError } from 'axios'
import { getJvagentUrl, getJvagentTimeout, getConfigAsync } from './config'
import { getToken, removeToken } from '../utils/storage'
import type {
  LoginRequest,
  LoginResponse,
  AgentsResponse,
  InteractionRequest,
  InteractionResponse,
} from '../types/api'

class ApiClient {
  private client: AxiosInstance
  private baseUrls: string[]
  private resolvedLoginPath?: string

  constructor() {
    // Initialize with default config, will be updated when config loads
    const baseURL = getJvagentUrl()
    this.baseUrls = this._buildBaseUrls(baseURL)
    console.log('API Client initialized with baseURLs:', this.baseUrls)
    
    this.client = axios.create({
      baseURL: baseURL,
      timeout: getJvagentTimeout(),
      headers: {
        'Content-Type': 'application/json',
      },
      // Enable cookies for session-based auth; harmless for bearer-token flows.
      withCredentials: true,
    })
    
    // Update baseURL when async config loads
    getConfigAsync().then((config) => {
      if (config.jvagent.url !== baseURL) {
        this.baseUrls = this._buildBaseUrls(config.jvagent.url)
        console.log('Updating API client baseURLs to:', this.baseUrls)
        this.client.defaults.baseURL = this.baseUrls[0]
        this.client.defaults.timeout = config.jvagent.timeout
      }
    }).catch((err) => {
      console.warn('Failed to load async config:', err)
    })

    // Request interceptor to add JWT token
    this.client.interceptors.request.use(
      (config) => {
        const token = getToken()
        if (token) {
          config.headers.Authorization = `Bearer ${token}`
          console.log('Request with token:', {
            url: config.url,
            method: config.method,
            hasToken: !!token,
            tokenPreview: token.substring(0, 20) + '...'
          })
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

    // Response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
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
          console.error('2. CORS is blocking the request')
          console.error('3. Wrong URL:', baseURL)
          console.error('4. Network connectivity issue')
        }
        
        if (error.response?.status === 401) {
          // Token expired or invalid - clear token and redirect to login
          removeToken()
          // Use replace to avoid adding to history
          if (window.location.pathname !== '/login') {
            window.location.replace('/login')
          }
        }
        return Promise.reject(error)
      }
    )
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
    let lastError: any
    for (const baseURL of this.baseUrls) {
      try {
        return await fn(baseURL)
      } catch (error: any) {
        lastError = error
        console.warn('Request failed for baseURL', baseURL, 'error:', error?.message || error)
        // Try next baseURL
      }
    }
    throw lastError
  }

  async login(credentials: LoginRequest): Promise<LoginResponse> {
    try {
      // If we already found a working login path, use it directly (single call)
      if (this.resolvedLoginPath) {
        const response = await this._withFallback((baseURL) =>
          this.client.post<any>(this.resolvedLoginPath!, credentials, { baseURL })
        )
        return this._extractLoginResponse(response)
      }

      // Otherwise, try path + base fallbacks and remember the first successful path
      const loginPaths = ['/api/auth/login', '/auth/login']
      let lastError: any

      for (const baseURL of this.baseUrls) {
        for (const path of loginPaths) {
          try {
            const response = await this.client.post<any>(path, credentials, { baseURL })
            this.resolvedLoginPath = path
            return this._extractLoginResponse(response)
          } catch (err: any) {
            lastError = err
            if (err.response?.status === 404) {
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

    // Handle direct TokenResponse
    if (payload.access_token && payload.token_type) {
      return payload as LoginResponse
    }

    throw new Error(
      payload.detail || payload.message || 'Unexpected login response format'
    )
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

  async deleteConversation(agentId: string, sessionId: string): Promise<void> {
    // Try multiple endpoint patterns to find the correct one
    let lastError: any
    const endpoints = [
      `/api/agents/${agentId}/conversations/${sessionId}`,
      `/agents/${agentId}/conversations/${sessionId}`,
      `/api/agents/${agentId}/sessions/${sessionId}`,
      `/agents/${agentId}/sessions/${sessionId}`,
    ]

    for (const baseURL of this.baseUrls) {
      for (const endpoint of endpoints) {
        try {
          await this.client.delete(endpoint, { baseURL })
          return // Success - exit early
        } catch (err: any) {
          lastError = err
          // If it's a 404, try next endpoint
          if (err.response?.status === 404) {
            continue
          }
          // For other errors, throw immediately
          throw err
        }
      }
    }

    // If all endpoints returned 404, that's okay - conversation might not exist on server
    // This is not a critical error, so we don't throw
    if (lastError?.response?.status === 404) {
      console.warn(`Conversation ${sessionId} not found on server (may have been deleted already)`)
      return
    }

    // For other errors, throw
    throw lastError || new Error('Failed to delete conversation')
  }
}

export const apiClient = new ApiClient()

