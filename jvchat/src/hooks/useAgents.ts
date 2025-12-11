import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../config/api'
import type { Agent } from '../types/agent'

export function useAgents(enabled?: boolean) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAgents = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiClient.getAgents(enabled)
      setAgents(response.agents || [])
    } catch (err: any) {
      console.error('Error fetching agents:', err)
      const errorMessage = 
        err.response?.data?.detail || 
        err.response?.data?.message || 
        err.message || 
        'Failed to load agents'
      setError(errorMessage)
    } finally {
      setLoading(false)
    }
  }, [enabled])

  useEffect(() => {
    fetchAgents()
  }, [fetchAgents])

  return {
    agents,
    loading,
    error,
    refresh: fetchAgents,
  }
}

