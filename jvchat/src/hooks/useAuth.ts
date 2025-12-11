import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '../config/api'
import { setToken, removeToken, getToken } from '../utils/storage'
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

