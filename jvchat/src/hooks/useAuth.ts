import { useState, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '../config/api'
import { setToken, removeToken, getToken, removeUserId, setUserId } from '../utils/storage'
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

  // Check token expiration on mount and periodically
  useEffect(() => {
    const checkToken = () => {
      const token = getToken()
      if (!token) {
        setState((prev) => ({ ...prev, isAuthenticated: false }))
        return
      }

      try {
        // Decode JWT to check expiration (without verification)
        const payload = JSON.parse(atob(token.split('.')[1]))
        const exp = payload.exp * 1000 // Convert to milliseconds
        const now = Date.now()

        if (exp < now) {
          // Token expired
          removeToken()
          setState((prev) => ({ ...prev, isAuthenticated: false }))
          if (window.location.pathname !== '/login') {
            navigate('/login', { replace: true })
          }
        }
      } catch (e) {
        // Invalid token format
        removeToken()
        setState((prev) => ({ ...prev, isAuthenticated: false }))
      }
    }

    checkToken()
    // Check every 30 seconds
    const interval = setInterval(checkToken, 30000)
    return () => clearInterval(interval)
  }, [navigate])

  const login = useCallback(
    async (credentials: LoginRequest) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))
      try {
        const response = await apiClient.login(credentials)
        console.log('Login response received:', response)
        console.log('Access token to store:', response.access_token)
        
        if (!response.access_token) {
          throw new Error('No access token received from server')
        }
        
        setToken(response.access_token)
        const storedToken = getToken()
        console.log('Token stored, verification:', storedToken ? 'Success' : 'Failed')
        console.log('Stored token preview:', storedToken ? storedToken.substring(0, 20) + '...' : 'None')
        
        // Store the logged-in user's account ID as user_id for chat system
        if (response.user?.id) {
          setUserId(response.user.id)
          console.log('Stored user_id from login:', response.user.id)
        } else {
          console.warn('Login response did not include user.id')
        }
        
        setState({
          isAuthenticated: true,
          loading: false,
          error: null,
        })
        navigate('/agents')
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

  const logout = useCallback(() => {
    removeToken()
    removeUserId() // Clear user_id on logout
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

