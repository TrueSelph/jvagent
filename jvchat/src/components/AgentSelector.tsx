import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAgents } from '../hooks/useAgents'
import { saveSelectedAgent } from '../utils/storage'
import type { Agent } from '../types/agent'

export function AgentSelector() {
  const navigate = useNavigate()
  const { agents, loading, error } = useAgents(true)
  const [searchQuery, setSearchQuery] = useState('')

  const filteredAgents = agents.filter((agent) => {
    if (!searchQuery) return true
    const query = searchQuery.toLowerCase()
    return (
      agent.name.toLowerCase().includes(query) ||
      agent.alias?.toLowerCase().includes(query) ||
      agent.description?.toLowerCase().includes(query)
    )
  })

  const handleSelectAgent = (agent: Agent) => {
    saveSelectedAgent(agent.name || agent.id)
    navigate(`/chat/${agent.id}`)
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading agents...</p>
        </div>
      </div>
    )
  }

  if (error) {
    const isNetworkError = error.toLowerCase().includes('network') || 
                          error.toLowerCase().includes('failed to fetch') ||
                          error.toLowerCase().includes('cors')
    
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4">
        <div className="max-w-md w-full">
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h2 className="text-xl font-bold text-red-600 mb-4">Error Loading Agents</h2>
            <p className="text-gray-700 mb-4">{error}</p>
            
            {isNetworkError && (
              <div className="bg-yellow-50 border border-yellow-200 rounded p-4 mb-4">
                <h3 className="font-semibold text-yellow-800 mb-2">Network Error Troubleshooting:</h3>
                <ul className="list-disc list-inside text-sm text-yellow-700 space-y-1">
                  <li>Ensure jvagent server is running</li>
                  <li>Check browser console (F12) for detailed error messages</li>
                  <li>Verify CORS is enabled on the jvagent server</li>
                  <li>Test server connection: Open <a href={`${import.meta.env.VITE_JVAGENT_URL || 'http://localhost:8000'}/health`} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">/health</a> in a new tab</li>
                  <li>Check that the server URL is correct in your configuration</li>
                </ul>
                <div className="mt-3 p-2 bg-gray-100 rounded text-xs font-mono">
                  Server URL: {import.meta.env.VITE_JVAGENT_URL || 'http://localhost:8000'}
                </div>
              </div>
            )}
            
            <button
              onClick={() => window.location.reload()}
              className="w-full px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 py-4 sm:py-8">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mb-4 sm:mb-6">
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 mb-2">
            Select an Agent
          </h1>
          <p className="text-sm sm:text-base text-gray-600">
            Choose an agent to start a conversation
          </p>
        </div>

        <div className="mb-4 sm:mb-6">
          <input
            type="text"
            placeholder="Search agents..."
            className="w-full px-4 py-3 text-base border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 touch-manipulation"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {filteredAgents.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-gray-500">
              {searchQuery ? 'No agents found matching your search' : 'No agents available'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4">
            {filteredAgents.map((agent) => (
              <button
                key={agent.id}
                onClick={() => handleSelectAgent(agent)}
                className="bg-white rounded-lg shadow p-4 sm:p-6 hover:shadow-lg transition-shadow text-left w-full touch-manipulation"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-base sm:text-lg font-semibold text-gray-900 flex-1 pr-2">
                    {agent.alias || agent.name || 'Unnamed Agent'}
                  </h3>
                  {agent.enabled !== false ? (
                    <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded flex-shrink-0">
                      Enabled
                    </span>
                  ) : (
                    <span className="px-2 py-1 text-xs bg-gray-100 text-gray-800 rounded flex-shrink-0">
                      Disabled
                    </span>
                  )}
                </div>
                {agent.description ? (
                  <p className="text-sm text-gray-600 mb-3 line-clamp-2 sm:line-clamp-3">
                    {agent.description}
                  </p>
                ) : (
                  <p className="text-sm text-gray-400 mb-3 italic">
                    No description available
                  </p>
                )}
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between text-xs gap-1 sm:gap-0">
                  <div className="flex flex-col">
                    <span className="text-gray-500 truncate">ID: {agent.id.substring(0, 20)}...</span>
                    {agent.namespace && (
                      <span className="text-gray-400">Namespace: {agent.namespace}</span>
                    )}
                    {agent.name && agent.name !== (agent.alias || agent.name) && (
                      <span className="text-gray-400 truncate">Name: {agent.name}</span>
                    )}
                  </div>
                  {agent.interaction_limit !== undefined && agent.interaction_limit > 0 && (
                    <span className="text-gray-500">
                      Limit: {agent.interaction_limit}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

