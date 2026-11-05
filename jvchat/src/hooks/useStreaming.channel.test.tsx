import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStreaming } from './useStreaming'
import { apiClient } from '../config/api'
import { getEffectiveUserId, saveMessages } from '../utils/storage'

vi.mock('../config/api', () => ({ apiClient: { streamInteract: vi.fn(), streamReplies: vi.fn() } }))
vi.mock('../utils/storage', async (original) => ({
  ...await original<typeof import('../utils/storage')>(),
  getToken: () => 'synthetic-login', getMessages: vi.fn(() => []), saveMessages: vi.fn(),
  getEffectiveUserId: vi.fn(() => 'user'), syncUserIdFromAccessToken: () => 'user',
}))

describe('background session ownership', () => {
  const connections: { callback: (frame: any) => void; signal: AbortSignal }[] = []
  beforeEach(() => {
    vi.clearAllMocks(); connections.length = 0
    vi.mocked(getEffectiveUserId).mockReturnValue('user')
    vi.mocked(apiClient.streamReplies).mockImplementation(async (_agent, _session, callback, signal) => {
      connections.push({ callback, signal })
      await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }))
    })
  })
  const message = { id: 'background', session_id: 'one', interaction_id: 'task-run', content: 'Scheduled result', message_type: 'adhoc', metadata: {}, category: 'user' }
  it('receives once, survives local hydration, and retires late old-session frames', async () => {
    const { result, rerender, unmount } = renderHook(({ session }) => useStreaming('agent', session), { initialProps: { session: 'one' } })
    await waitFor(() => expect(connections).toHaveLength(1))
    act(() => { connections[0].callback(message); connections[0].callback(message) })
    expect(result.current.messages).toHaveLength(1)
    act(() => result.current.loadMessages([{ id: 'old', role: 'user', content: 'Earlier', timestamp: '2026-11-05T00:00:00Z' }]))
    expect(result.current.messages.map(m => m.content)).toEqual(['Earlier', 'Scheduled result'])
    rerender({ session: 'two' })
    act(() => connections[0].callback({ ...message, id: 'late' }))
    expect(connections[0].signal.aborted).toBe(true)
    expect(result.current.messages).toHaveLength(0)
    unmount()
    expect(connections[connections.length - 1].signal.aborted).toBe(true)
  })
  it('suspends for a foreground request and ignores delivery callbacks during send preparation', async () => {
    const { result, unmount } = renderHook(() => useStreaming('agent', 'one'))
    await waitFor(() => expect(connections).toHaveLength(1))
    let finish: () => void = () => {}
    vi.mocked(apiClient.streamInteract).mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
    let send: Promise<string | undefined>
    act(() => { send = result.current.sendMessage('Hello') })
    await waitFor(() => expect(connections[0].signal.aborted).toBe(true))
    act(() => connections[0].callback(message))
    expect(result.current.messages.some(m => m.id === 'background')).toBe(false)
    await act(async () => { finish(); await send! })
    unmount()
  })
  it('waits for actual request completion before replay and excludes foreground-origin frames', async () => {
    const { result, unmount } = renderHook(() => useStreaming('agent', 'one'))
    await waitFor(() => expect(connections).toHaveLength(1))
    let complete: () => void = () => {}
    vi.mocked(apiClient.streamInteract).mockImplementation(async (_agent, _request, onChunk) => {
      onChunk({ type: 'final', session_id: 'one', response: 'Answer', interaction: { id: 'foreground', response: 'Answer' } })
      await new Promise<void>(resolve => { complete = resolve })
    })
    let send: Promise<string | undefined>
    act(() => { send = result.current.sendMessage('Hello') })
    await waitFor(() => expect(connections[0].signal.aborted).toBe(true))
    expect(connections).toHaveLength(1)
    await act(async () => { complete(); await send! })
    await waitFor(() => expect(connections).toHaveLength(2))
    act(() => {
      connections[1].callback({ ...message, id: 'old-foreground', metadata: { is_proactive: false } })
      connections[1].callback({ ...message, metadata: { is_proactive: true } })
    })
    expect(result.current.messages.some(m => m.id === 'old-foreground')).toBe(false)
    expect(result.current.messages.some(m => m.id === 'background')).toBe(true)
    act(() => {
      const restored = { ...message, id: 'restored-foreground', interaction_id: 'foreground', content: 'Recovered answer', metadata: { native_replay: true, is_proactive: false } }
      connections[1].callback(restored)
      connections[1].callback(restored)
    })
    expect(result.current.messages.filter(m => m.content === 'Recovered answer')).toHaveLength(1)
    unmount()
  })
  it('retires delivery and clears the transcript when the authenticated caller changes', async () => {
    const { result, rerender, unmount } = renderHook(() => useStreaming('agent', 'one'))
    await waitFor(() => expect(connections).toHaveLength(1))
    act(() => connections[0].callback(message))
    expect(result.current.messages).toHaveLength(1)
    vi.mocked(saveMessages).mockClear()
    vi.mocked(getEffectiveUserId).mockReturnValue('another-user')
    act(() => connections[0].callback({ ...message, id: 'late' }))
    rerender()
    expect(connections[0].signal.aborted).toBe(true)
    expect(result.current.messages).toHaveLength(0)
    expect(saveMessages).not.toHaveBeenCalled()
    unmount()
  })
})
