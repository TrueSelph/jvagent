export interface JvagentConfig {
  url: string
  timeout: number
}

export interface UIConfig {
  theme: 'light' | 'dark'
  messages_per_page: number
  auto_scroll: boolean
}

export interface AppConfig {
  jvagent: JvagentConfig
  ui: UIConfig
}

const DEFAULT_CONFIG: AppConfig = {
  jvagent: {
    url: import.meta.env.VITE_JVAGENT_URL || 'http://localhost:8000',
    timeout: 30000,
  },
  ui: {
    theme: 'light',
    messages_per_page: 50,
    auto_scroll: true,
  },
}

let cachedConfig: AppConfig | null = null
let configLoadPromise: Promise<AppConfig> | null = null

async function loadConfigFromFile(): Promise<Partial<AppConfig>> {
  if (typeof window === 'undefined') return {}
  
  try {
    // Try to load from config.json (browser-readable version of config.yaml)
    const response = await fetch('/config.json')
    if (response.ok) {
      const config = await response.json()
      return config as Partial<AppConfig>
    }
  } catch (error) {
    // config.json not found, that's okay
    console.debug('config.json not found, using defaults')
  }
  
  return {}
}

function loadConfigFromStorage(): Partial<AppConfig> {
  if (typeof window === 'undefined') return {}
  
  try {
    const stored = localStorage.getItem('jvchat_config')
    if (stored) {
      return JSON.parse(stored) as Partial<AppConfig>
    }
  } catch (error) {
    console.warn('Failed to load config from localStorage:', error)
  }
  
  return {}
}

export async function getConfigAsync(): Promise<AppConfig> {
  if (cachedConfig) {
    return cachedConfig
  }
  
  if (configLoadPromise) {
    return configLoadPromise
  }
  
  configLoadPromise = (async () => {
    const fileConfig = await loadConfigFromFile()
    const storedConfig = loadConfigFromStorage()
    
    cachedConfig = {
      jvagent: {
        ...DEFAULT_CONFIG.jvagent,
        ...fileConfig.jvagent,
        ...storedConfig.jvagent,
      },
      ui: {
        ...DEFAULT_CONFIG.ui,
        ...fileConfig.ui,
        ...storedConfig.ui,
      },
    }
    
    return cachedConfig
  })()
  
  return configLoadPromise
}

export function getConfig(): AppConfig {
  if (cachedConfig) {
    return cachedConfig
  }

  // Fallback to synchronous loading if async hasn't completed
  const storedConfig = loadConfigFromStorage()
  cachedConfig = {
    jvagent: {
      ...DEFAULT_CONFIG.jvagent,
      ...storedConfig.jvagent,
    },
    ui: {
      ...DEFAULT_CONFIG.ui,
      ...storedConfig.ui,
    },
  }

  return cachedConfig
}

export function saveConfig(config: Partial<AppConfig>): void {
  if (typeof window === 'undefined') return
  
  try {
    const current = getConfig()
    const updated = {
      jvagent: { ...current.jvagent, ...config.jvagent },
      ui: { ...current.ui, ...config.ui },
    }
    localStorage.setItem('jvchat_config', JSON.stringify(updated))
    cachedConfig = updated
  } catch (error) {
    console.error('Failed to save config:', error)
  }
}

export function getJvagentUrl(): string {
  return getConfig().jvagent.url
}

export function getJvagentTimeout(): number {
  return getConfig().jvagent.timeout
}

