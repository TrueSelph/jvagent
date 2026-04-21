export interface JvagentConfig {
  url: string
  timeout: number
}

/** Standalone jvforge service (async jobs, ``/v1/queue``, ``/v1/jobs``, …) — not the jvagent host. */
export interface JvforgeConfig {
  url: string
}

export interface UIConfig {
  theme: 'light' | 'dark'
  messages_per_page: number
  auto_scroll: boolean
}

export interface AppConfig {
  jvagent: JvagentConfig
  jvforge: JvforgeConfig
  ui: UIConfig
}

const DEFAULT_CONFIG: AppConfig = {
  jvagent: {
    url: import.meta.env.VITE_JVAGENT_URL || 'http://localhost:8000',
    timeout: 900000, // 15 minutes
  },
  jvforge: {
    url:
      import.meta.env.VITE_JVFORGE_URL ||
      import.meta.env.VITE_JVAGENT_JVFORGE_BASE_URL ||
      'http://127.0.0.1:8088',
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
  // File-based config loading removed - configuration is now handled via:
  // 1. User input from login screen (saved to localStorage)
  // 2. Environment variables (VITE_JVAGENT_URL)
  // 3. Default values
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
      jvforge: {
        ...DEFAULT_CONFIG.jvforge,
        ...fileConfig.jvforge,
        ...storedConfig.jvforge,
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
    jvforge: {
      ...DEFAULT_CONFIG.jvforge,
      ...storedConfig.jvforge,
    },
    ui: {
      ...DEFAULT_CONFIG.ui,
      ...storedConfig.ui,
    },
  }

  return cachedConfig
}

export function saveConfig(config: {
  jvagent?: Partial<JvagentConfig>
  jvforge?: Partial<JvforgeConfig>
  ui?: Partial<UIConfig>
}): void {
  if (typeof window === 'undefined') return

  try {
    const current = getConfig()
    const updated = {
      jvagent: { ...current.jvagent, ...config.jvagent },
      jvforge: { ...current.jvforge, ...config.jvforge },
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

export function getJvforgeUrl(): string {
  return getConfig().jvforge.url.replace(/\/$/, '')
}

