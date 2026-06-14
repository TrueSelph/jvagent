export interface Agent {
  id: string
  entity?: string
  namespace: string
  name: string
  alias?: string
  avatar_url?: string
  enabled: boolean
  description?: string
  interaction_limit?: number
  context?: {
    namespace?: string
    name?: string
    alias?: string
    enabled?: boolean
    description?: string
    interaction_limit?: number
  }
}

