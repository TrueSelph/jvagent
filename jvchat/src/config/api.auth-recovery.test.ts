import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'

vi.mock('./config', () => ({
  getJvagentUrl: () => 'http://127.0.0.1:8136',
  getJvforgeUrl: () => 'http://127.0.0.1:8088',
  getJvagentTimeout: () => 1000,
  getConfigAsync: async () => ({ jvagent: { url: 'http://127.0.0.1:8136' }, jvforge: { url: 'http://127.0.0.1:8088' } }),
}))

import { clearAuthSession, getRefreshToken, getToken, getUserId, setInteractSessionToken, setRefreshToken, setToken, setUserId } from '../utils/storage'

const token = (seconds: number, user = 'caller') => `synthetic.${btoa(JSON.stringify({ user_id: user, exp: Date.now() / 1000 + seconds }))}.signature`

describe('login refresh preserves caller and request ownership', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    vi.resetModules(); localStorage.clear(); fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    setToken(token(-60)); setRefreshToken('synthetic-refresh'); setUserId('caller')
  })

  it.each(['network', 429, 503, 'malformed'])('retains credentials and does not submit interact after refresh failure: %s', async failure => {
    const { apiClient } = await import('./api')
    const original = getToken()
    fetchMock.mockImplementation(async () => {
      if (failure === 'network') throw new TypeError('Failed to fetch')
      return new Response(failure === 'malformed' ? '{}' : 'Temporarily unavailable', { status: typeof failure === 'number' ? failure : 200 })
    })
    await expect(apiClient.streamReplies('agent', 'session', vi.fn(), new AbortController().signal)).rejects.toThrow()
    expect(getToken()).toBe(original)
    expect(getRefreshToken()).toBe('synthetic-refresh')
    expect(getUserId()).toBe('caller')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/auth/refresh')
  })

  it('refreshes before a paid interaction rather than dropping its expired bearer', async () => {
    const { apiClient } = await import('./api')
    const fresh = token(3600)
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ access_token: fresh, token_type: 'bearer', refresh_token: 'rotated-refresh' })))
    fetchMock.mockResolvedValueOnce(new Response('{"response":"ok"}'))
    await apiClient.interact('agent', { utterance: 'Hello' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/auth/refresh')
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${fresh}`)
  })

  it('never refreshes or retries an authenticated POST after opaque network failure', async () => {
    const { apiClient } = await import('./api')
    setToken(token(3600))
    const client = apiClient.client
    const adapter = vi.fn(async config => { throw new axios.AxiosError('Network Error', 'ERR_NETWORK', config) })
    client.defaults.adapter = adapter
    await expect(client.post('/api/actions/synthetic/write', {})).rejects.toThrow('Network Error')
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(getRefreshToken()).toBe('synthetic-refresh')
  })

  it.each([401, 403])('clears only explicitly rejected current credentials: %s', async status => {
    const { apiClient } = await import('./api')
    fetchMock.mockResolvedValue(new Response('', { status }))
    await expect(apiClient.streamReplies('agent', 'session', vi.fn(), new AbortController().signal)).rejects.toThrow('rejected')
    expect(getToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
    expect(getUserId()).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shares one refresh across concurrent interaction preparations', async () => {
    const { apiClient } = await import('./api')
    let finish: (response: Response) => void = () => {}
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
    const first = apiClient.interact('agent', { utterance: 'First' })
    const second = apiClient.interact('agent', { utterance: 'Second' })
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const fresh = token(3600)
    fetchMock.mockImplementation(async () => new Response('{"response":"ok"}'))
    finish(new Response(JSON.stringify({ access_token: fresh, token_type: 'bearer' })))
    await Promise.all([first, second])
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls.slice(1).every(([, options]) => options.headers.Authorization === `Bearer ${fresh}`)).toBe(true)
  })

  it.each([false, true])('late refresh completion cannot replace or clear another login (rejected=%s)', async rejected => {
    const { apiClient } = await import('./api')
    let finish: (response: Response) => void = () => {}
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
    const pending = apiClient.refreshAuthentication()
    const changed = token(3600, 'another-caller')
    setToken(changed); setRefreshToken('another-refresh'); setUserId('another-caller')
    finish(rejected ? new Response('', { status: 401 }) : new Response(JSON.stringify({ access_token: token(3600), token_type: 'bearer' })))
    await expect(pending).rejects.toThrow()
    expect(getToken()).toBe(changed)
    expect(getRefreshToken()).toBe('another-refresh')
    expect(getUserId()).toBe('another-caller')
  })

  it('does not clear or refresh the login for a permission denial', async () => {
    const { apiClient } = await import('./api')
    setToken(token(3600))
    apiClient.client.defaults.adapter = async config => { throw new axios.AxiosError('Denied', 'ERR_BAD_REQUEST', config, undefined, { status: 403, data: {}, statusText: 'Forbidden', headers: {}, config }) }
    await expect(apiClient.client.get('/api/admin-only')).rejects.toThrow('Denied')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(getRefreshToken()).toBe('synthetic-refresh')
  })

  it('lets an interaction cancel while a shared login refresh is still pending', async () => {
    const { apiClient } = await import('./api')
    const controller = new AbortController()
    let finish: (response: Response) => void = () => {}
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
    const request = apiClient.streamInteract('agent', { utterance: 'Cancelled' }, vi.fn(), undefined, controller.signal)
    controller.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
    const fresh = token(3600)
    finish(new Response(JSON.stringify({ access_token: fresh, token_type: 'bearer' })))
    await vi.waitFor(() => expect(getToken()).toBe(fresh))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('bounds a stalled refresh without clearing credentials or submitting an interaction', async () => {
    const { apiClient } = await import('./api')
    vi.useFakeTimers()
    try {
      fetchMock.mockImplementation((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))))
      const request = expect(apiClient.interact('agent', { utterance: 'Hello' })).rejects.toThrow('temporarily unavailable')
      await vi.advanceTimersByTimeAsync(15_000)
      await request
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(getRefreshToken()).toBe('synthetic-refresh')
      expect(getUserId()).toBe('caller')
    } finally { vi.useRealTimers() }
  })

  it('does not clear a new login when an older authenticated request returns 401', async () => {
    const { apiClient } = await import('./api')
    setToken(token(3600))
    let fail: () => void = () => {}
    let ready = false
    apiClient.client.defaults.adapter = config => new Promise((_resolve, reject) => {
      ready = true
      fail = () => reject(new axios.AxiosError('Old login refused', 'ERR_BAD_REQUEST', config, undefined, { status: 401, data: {}, statusText: 'Unauthorized', headers: {}, config }))
    })
    const request = apiClient.client.get('/api/agents')
    await vi.waitFor(() => expect(ready).toBe(true))
    const changed = token(3600, 'another-caller')
    setToken(changed); setRefreshToken('another-refresh'); setUserId('another-caller')
    fail()
    await expect(request).rejects.toThrow('Old login refused')
    expect(getToken()).toBe(changed)
    expect(getRefreshToken()).toBe('another-refresh')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('drops a refresh from a server that is no longer selected', async () => {
    const { apiClient } = await import('./api')
    let finish: (response: Response) => void = () => {}
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
    const original = getToken()
    const pending = apiClient.refreshAuthentication()
    apiClient.updateBaseUrl('http://127.0.0.1:9999')
    finish(new Response(JSON.stringify({ access_token: token(3600), token_type: 'bearer' })))
    await expect(pending).rejects.toThrow('changed')
    expect(getToken()).toBe(original)
    expect(getRefreshToken()).toBe('synthetic-refresh')
  })

  it.each([false, true])('does not repeat an unknown write against host aliases after logout (server error=%s)', async serverError => {
    const { apiClient } = await import('./api')
    apiClient.updateBaseUrl('http://localhost:8136')
    setToken(token(3600))
    const adapter = vi.fn(async config => {
      clearAuthSession()
      throw new axios.AxiosError('Unknown write outcome', 'ERR_NETWORK', config, undefined,
        serverError ? { status: 500, data: {}, statusText: 'Server error', headers: {}, config } : undefined)
    })
    apiClient.client.defaults.adapter = adapter
    await expect(apiClient.updateAction('synthetic', { enabled: true })).rejects.toThrow('Unknown write outcome')
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps explicit auth route-not-found compatibility', async () => {
    const { apiClient } = await import('./api')
    fetchMock.mockResolvedValueOnce(new Response('', { status: 404 }))
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ access_token: token(3600), token_type: 'bearer' })))
    fetchMock.mockResolvedValueOnce(new Response('{"response":"ok"}'))
    await apiClient.interact('agent', { utterance: 'Hello' })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      'http://127.0.0.1:8136/api/auth/refresh', 'http://127.0.0.1:8136/auth/refresh',
      'http://127.0.0.1:8136/api/agents/agent/interact',
    ])
  })

  it.each(['frame', 'http'])('renews an expired parked bearer after %s refusal without interact', async refusal => {
    const { apiClient } = await import('./api')
    vi.useFakeTimers()
    try {
      const old = token(60), renewed = token(3600)
      setToken(old)
      fetchMock.mockImplementationOnce(async () => {
        vi.setSystemTime(Date.now() + 61_000)
        return refusal === 'http' ? new Response('', { status: 401 }) : new Response('data: {"type":"error","code":"session_access_revoked"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      })
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ access_token: renewed, token_type: 'bearer' })))
      fetchMock.mockResolvedValueOnce(new Response('data: {"id":"reply","session_id":"session","message_type":"adhoc","content":"Scheduled result"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } }))
      const receive = vi.fn()
      await apiClient.streamReplies('agent', 'session', receive, new AbortController().signal)
      expect(fetchMock).toHaveBeenCalledTimes(3)
      expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
        'http://127.0.0.1:8136/api/agents/agent/reply/subscribe',
        'http://127.0.0.1:8136/api/auth/refresh',
        'http://127.0.0.1:8136/api/agents/agent/reply/subscribe',
      ])
      expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe(`Bearer ${renewed}`)
      expect(receive).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

  it.each([401, 403])('does not renew an unexpired bearer refused with %s', async status => {
    const { apiClient } = await import('./api')
    const current = token(3600)
    setToken(current)
    fetchMock.mockResolvedValueOnce(new Response('', { status }))
    await expect(apiClient.streamReplies('agent', 'session', vi.fn(), new AbortController().signal)).rejects.toThrow(status === 403 ? 'Forbidden' : 'Unauthorized')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getToken()).toBe(current)
  })

  it('does not renew a revoked Mode B capability', async () => {
    const { apiClient } = await import('./api')
    clearAuthSession(); setUserId('guest')
    setInteractSessionToken('session', token(3600, 'guest'))
    fetchMock.mockResolvedValueOnce(new Response('data: {"type":"error","code":"session_access_revoked"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } }))
    await expect(apiClient.streamReplies('agent', 'session', vi.fn(), new AbortController().signal)).rejects.toThrow('Unauthorized')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not reconnect on a stale renewal result', async () => {
    const { apiClient } = await import('./api')
    vi.useFakeTimers()
    try {
      const old = token(60); setToken(old)
      fetchMock.mockImplementationOnce(async () => { vi.setSystemTime(Date.now() + 61_000); return new Response('', { status: 401 }) })
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ access_token: old, token_type: 'bearer' })))
      await expect(apiClient.streamReplies('agent', 'session', vi.fn(), new AbortController().signal)).rejects.toThrow('Unauthorized')
      expect(fetchMock).toHaveBeenCalledTimes(2)
    } finally { vi.useRealTimers() }
  })
})
