export interface Agent {
  id: string
  namespace: string
  name: string
  alias?: string
  enabled: boolean
  description?: string
  interaction_limit?: number
}

