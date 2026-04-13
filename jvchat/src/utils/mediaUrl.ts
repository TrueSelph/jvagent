import { getJvagentUrl } from '../config/config'

/** Turn relative paths (e.g. /api/files/...) into absolute URLs for img/video/audio src. */
export function resolveMediaUrl(url: string): string {
  const trimmed = (url || '').trim()
  if (!trimmed.startsWith('/')) return trimmed
  const base = getJvagentUrl().replace(/\/+$/, '')
  return base ? `${base}${trimmed}` : trimmed
}
