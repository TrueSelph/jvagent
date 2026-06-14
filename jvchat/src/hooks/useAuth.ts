import { useState, useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '../config/api'
import {
  setToken,
  getToken,
  setUserId,
  setRefreshToken,
  getRefreshToken,
  clearAuthSession,
} from '../utils/storage'
import { saveConfig } from '../config/config'
import type { LoginRequest } from '../types/api'

interface AuthState {
  isAuthenticated: boolean
  loading: boolean
  error: string | null
}

export function useAuth() {
  const navigate = useNavigate()
  const [state, setState] = useState<AuthState>({
    isAuthenticated: !!getToken(),
    loading: false,
    error: null,
  })
  const refreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Proactive token refresh: check token expiration and refresh before it expires
  useEffect(() => {
    const checkAndRefreshToken = async () => {
      const token = getToken()
      const refreshToken = getRefreshToken()

      if (!token || !refreshToken) {
        setState((prev) => ({ ...prev, isAuthenticated: false }))
        return
      }

      try {
        // Decode JWT to check expiration (without verification)
        const payload = JSON.parse(atob(token.split('.')[1]))
        const exp = payload.exp * 1000 // Convert to milliseconds
        const now = Date.now()
        const timeUntilExpiry = exp - now

        // If token is already expired, let the API client handle it via 401 interceptor
        if (timeUntilExpiry <= 0) {
          return
        }

        // If token expires within 5 minutes, refresh proactively
        const fiveMinutes = 5 * 60 * 1000
        if (timeUntilExpiry < fiveMinutes) {
          try {
            const refreshResponse = await apiClient.refreshToken({ refresh_token: refreshToken })
            setToken(refreshResponse.access_token)
            if (refreshResponse.refresh_token) {
              setRefreshToken(refreshResponse.refresh_token)
            }
            // Update user_id if provided in response
            if (refreshResponse.user?.id) {
              setUserId(refreshResponse.user.id)
            }
            console.log('Token refreshed proactively')

            // Reschedule next check with new token
            // Decode new token to get its expiration
            try {
              const newPayload = JSON.parse(atob(refreshResponse.access_token.split('.')[1]))
              const newExp = newPayload.exp * 1000
              const newTimeUntilExpiry = newExp - Date.now()
              const checkIn = Math.max(newTimeUntilExpiry - fiveMinutes, 60000) // At least 1 minute
              if (refreshTimeoutRef.current) {
                clearTimeout(refreshTimeoutRef.current)
              }
              refreshTimeoutRef.current = setTimeout(checkAndRefreshToken, checkIn)
            } catch (e) {
              // If we can't decode new token, schedule check in 2 minutes
              if (refreshTimeoutRef.current) {
                clearTimeout(refreshTimeoutRef.current)
              }
              refreshTimeoutRef.current = setTimeout(checkAndRefreshToken, 2 * 60 * 1000)
            }
          } catch (error) {
            // Refresh failed, but don't clear tokens here - let 401 interceptor handle it
            console.warn('Proactive token refresh failed:', error)
            // Schedule retry in 1 minute
            if (refreshTimeoutRef.current) {
              clearTimeout(refreshTimeoutRef.current)
            }
            refreshTimeoutRef.current = setTimeout(checkAndRefreshToken, 60000)
          }
        } else {
          // Schedule next check for 1 minute before expiration
          const checkIn = Math.max(timeUntilExpiry - fiveMinutes, 60000) // At least 1 minute
          if (refreshTimeoutRef.current) {
            clearTimeout(refreshTimeoutRef.current)
          }
          refreshTimeoutRef.current = setTimeout(checkAndRefreshToken, checkIn)
        }
      } catch (e) {
        // Invalid token format - let API client handle it
        console.warn('Token validation error:', e)
      }
    }

    // Initial check
    checkAndRefreshToken()

    // Also check periodically (every 2 minutes) as a fallback
    const interval = setInterval(checkAndRefreshToken, 2 * 60 * 1000)

    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current)
      }
      clearInterval(interval)
    }
  }, [])

  const login = useCallback(
    async (credentials: LoginRequest) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))
      try {
        // Save server URL to config if provided
        if (credentials.serverUrl) {
          saveConfig({ jvagent: { url: credentials.serverUrl } })
        }

        const response = await apiClient.login(credentials)
        if (import.meta.env.DEV) {
          console.log('Login response received:', response)
        }

        if (!response.access_token) {
          throw new Error('No access token received from server')
        }

        setToken(response.access_token)

        // Store refresh token if provided
        if (response.refresh_token) {
          setRefreshToken(response.refresh_token)
        } else if (import.meta.env.DEV) {
          console.warn('Login response did not include refresh_token')
        }

        // Store the logged-in user's account ID as user_id for chat system
        if (response.user?.id) {
          setUserId(response.user.id)
          if (import.meta.env.DEV) {
            console.log('Stored user_id from login:', response.user.id)
          }
        } else if (import.meta.env.DEV) {
          console.warn('Login response did not include user.id')
        }

        setState({
          isAuthenticated: true,
          loading: false,
          error: null,
        })
        navigate('/chat')
      } catch (error: any) {
        const errorMessage =
          error.response?.data?.detail || error.message || 'Login failed'
        setState({
          isAuthenticated: false,
          loading: false,
          error: errorMessage,
        })
        throw error
      }
    },
    [navigate]
  )

  const logout = useCallback(async () => {
    // First, try to revoke tokens on the server
    // This is best-effort - we'll clear local storage regardless of success/failure
    try {
      await apiClient.logout()
    } catch (error) {
      // Log error but continue with local logout
      console.warn('Logout: Server logout failed, continuing with local logout:', error)
    }

    // Clear credentials only — conversations/messages stay in localStorage for the same login.
    clearAuthSession()
    if (refreshTimeoutRef.current) {
      clearTimeout(refreshTimeoutRef.current)
    }
    setState({
      isAuthenticated: false,
      loading: false,
      error: null,
    })
    navigate('/login')
  }, [navigate])

  return {
    ...state,
    login,
    logout,
  }
}

