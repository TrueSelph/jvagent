import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../config/api'
import { useAuth } from './useAuth'
import { clearAllStorage } from '../utils/storage'
import type { Agent } from '../types/agent'

export function useAgents(enabled?: boolean) {
  const { logout } = useAuth()
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAgents = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiClient.getAgents(enabled)
      // Transform agents to flatten the context structure
      const transformedAgents = (response.agents || []).map((agent: any) => {
        // If agent has a context property, flatten it
        if (agent.context) {
          return {
            id: agent.id,
            entity: agent.entity,
            namespace: agent.context.namespace || agent.namespace || '',
            name: agent.context.name || agent.name || '',
            alias: agent.context.alias || agent.alias,
            enabled: agent.context.enabled !== undefined ? agent.context.enabled : (agent.enabled !== undefined ? agent.enabled : true),
            description: agent.context.description || agent.description,
            interaction_limit: agent.context.interaction_limit !== undefined ? agent.context.interaction_limit : (agent.interaction_limit !== undefined ? agent.interaction_limit : 0),
          }
        }
        // Otherwise return as-is
        return agent
      })
      setAgents(transformedAgents)
    } catch (err: any) {
      console.error('Error fetching agents:', err)
      // Clear all local storage and invalidate session
      clearAllStorage()
      // logout() will also navigate to login screen
      logout()
    } finally {
      setLoading(false)
    }
  }, [enabled, logout])

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

