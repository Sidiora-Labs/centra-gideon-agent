import { describe, expect, it, vi } from 'vitest'
import { createShellRoute, parseShellRoute } from '../../shared/shell/shellRoutes'
import { assistantReturnHref, preserveAssistantRoute, stripAssistantOAuthCallback } from './returnRoute.web'

describe('assistant return and OAuth callback routes', () => {
  it('preserves a direct record and workspace return across serialization and callback cleanup', () => {
    const route = createShellRoute('chat', {
      view: 'detail',
      record: { kind: 'chat_session', id: 'session/with space' },
      sessionId: 'session/with space',
      returnTo: { destination: 'activity', record: { kind: 'task', id: 'task-42' },
        placement: { id: 'console', query: { tab: 'history' } }, scrollY: 240 },
    })
    const path = preserveAssistantRoute(route)
    expect(parseShellRoute(path, 'https://gideon.example')).toEqual(route)

    const callbackUrl = new URL(path, 'https://gideon.example')
    callbackUrl.searchParams.set('code', 'authorization-code')
    callbackUrl.searchParams.set('state', 'one-time-state')
    callbackUrl.searchParams.set('error_description', 'private provider detail')
    const history = { state: { entry: 7 }, replaceState: vi.fn() }
    const callback = stripAssistantOAuthCallback(callbackUrl, history)

    expect(callback).toEqual({ kind: 'success', code: 'authorization-code', state: 'one-time-state' })
    const visibleUrl = String(history.replaceState.mock.calls[0]?.[2])
    expect(visibleUrl).not.toMatch(/authorization-code|one-time-state|private provider detail/)
    expect(parseShellRoute(visibleUrl, callbackUrl.origin)).toEqual(route)
    expect(history.replaceState).toHaveBeenCalledWith(history.state, '', visibleUrl)
    expect(assistantReturnHref(route)).toBe('/#/tasks/task-42?tab=history')
  })

  it('clears denied and malformed callbacks before exposing a recovery reason', () => {
    const url = new URL('/assistant/chat?v=1&error=access_denied&error_description=account%20closed&state=private', 'https://gideon.example')
    const history = { state: null, replaceState: vi.fn() }
    expect(stripAssistantOAuthCallback(url, history)).toEqual({ kind: 'denied', error: 'access_denied' })
    expect(String(history.replaceState.mock.calls[0]?.[2])).toBe('/assistant/chat?v=1')

    const duplicate = new URL('/assistant/chat?v=1&code=one&code=two&state=private', 'https://gideon.example')
    const duplicateHistory = { state: null, replaceState: vi.fn() }
    expect(stripAssistantOAuthCallback(duplicate, duplicateHistory)).toEqual({ kind: 'denied', error: 'invalid_callback' })
    expect(String(duplicateHistory.replaceState.mock.calls[0]?.[2])).toBe('/assistant/chat?v=1')
  })

  it('does not create a return link without a known workspace destination', () => {
    const route = createShellRoute('chat')
    expect(() => assistantReturnHref(route)).toThrow('A workspace return route is required')
  })
})
