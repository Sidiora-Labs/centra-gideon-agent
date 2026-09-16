import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook } from '@testing-library/react'
import {
  DEFAULT_NOTIFICATION_ROUTE,
  NOTIFICATION_SOURCE_ROUTES,
  routeForNote,
  shouldDeliverNatively,
  useNativeNotifications,
} from './nativeNotifications'


let deliver: ((m: { type: string; data: Record<string, unknown> }) => void) | null = null
vi.mock('./useChatSocket', () => ({
  useChatSocket: (cb: (m: { type: string; data: Record<string, unknown> }) => void) => {
    deliver = cb
  },
}))

function fakeBridge() {
  const shown: Array<{ title: string; body: string; route: string }> = []
  const taps: Array<(p: { route: string }) => void> = []
  let unsubscribed = 0
  const bridge = {
    capabilities: {
      names: () => [],
      probe: vi.fn(),
      snapshot: vi.fn(),
      request: vi.fn(),
      on: () => () => {},
    },
    notifications: {
      show: (note: { title: string; body: string; route: string }) => {
        shown.push(note)
        return Promise.resolve({ ok: true, route: note.route })
      },
      on: (cb: (p: { route: string }) => void) => {
        taps.push(cb)
        return () => { unsubscribed += 1 }
      },
    },
  }
  ;(window as unknown as { gideonDesktop: unknown }).gideonDesktop = bridge
  return { shown, tap: (p: { route: string }) => taps.forEach((t) => t(p)), unsubscribedCount: () => unsubscribed }
}

const note = (extra: Record<string, unknown> = {}) => ({
  kind: 'error', title: 'Loop stalled', body: 'needs an answer', ...extra,
})

beforeEach(() => { deliver = null })
afterEach(() => {
  delete (window as unknown as { gideonDesktop?: unknown }).gideonDesktop
  vi.restoreAllMocks()
})

describe('shouldDeliverNatively', () => {
  it('reads native.deliver, not the presence of the key', () => {
    expect(shouldDeliverNatively(note({ native: { deliver: true, reason: '' } }))).toBe(true)
    expect(shouldDeliverNatively(note({ native: { deliver: false, reason: 'not connected' } }))).toBe(false)
    expect(shouldDeliverNatively(note())).toBe(false)
    expect(shouldDeliverNatively(note({ native: true }))).toBe(false)
    expect(shouldDeliverNatively(note({ native: 'yes' }))).toBe(false)
  })
})

describe('routeForNote', () => {
  it('maps a rule source to a surface', () => {
    expect(routeForNote(note({ source: 'inbox' }))).toBe('inbox')
    expect(routeForNote(note({ source: 'loop' }))).toBe('loops')
    expect(routeForNote(note({ source: 'planning' }))).toBe('tasks')
  })

  it('falls back to the feed for an absent or unknown source', () => {
    expect(routeForNote(note())).toBe(DEFAULT_NOTIFICATION_ROUTE)
    expect(routeForNote(note({ source: 'not-a-source' }))).toBe(DEFAULT_NOTIFICATION_ROUTE)
    expect(routeForNote(note({ source: 7 }))).toBe(DEFAULT_NOTIFICATION_ROUTE)
  })

  it('covers every source the backend can send', () => {
    const py = readFileSync(join(__dirname, "../../../../../runtime/gideon/workspace/notification_kinds.py"), 'utf8')
    const sources = new Set(
      [...py.matchAll(/NotificationKind\(\s*"([a-z_]+)"/g)].map((m) => m[1]),
    )
    expect(sources.size).toBeGreaterThan(5)
    for (const s of sources) expect(Object.keys(NOTIFICATION_SOURCE_ROUTES)).toContain(s)
  })

  it('only ever names a route App.tsx actually serves', () => {
    const app = readFileSync(join(__dirname, "../../app/shell/App.tsx"), 'utf8')
    const nav = [...app.matchAll(/\{ id: '([a-z-]+)', label:/g)].map((m) => m[1])
    const routable = (app.match(/const ROUTABLE = new Set\(\[([^\]]*)\]/)?.[1] ?? '')
    const extras = [...routable.matchAll(/'([a-z-]+)'/g)].map((m) => m[1])
    const served = new Set([...nav, ...extras])
    expect(served.size).toBeGreaterThan(10)
    for (const route of Object.values(NOTIFICATION_SOURCE_ROUTES)) {
      expect([...served]).toContain(route)
    }
  })
})

describe('useNativeNotifications', () => {
  it('raises a banner for a note the gateway marked deliverable', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: note({ source: 'loop', native: { deliver: true, reason: '' } }) })
    expect(bridge.shown).toEqual([{ title: 'Loop stalled', body: 'needs an answer', route: 'loops' }])
  })

  it('🪤 VACUITY: raises nothing for a note the gateway did not mark', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: note({ source: 'loop' }) })
    deliver!({ type: 'notification', data: note({ source: 'loop', native: { deliver: false, reason: 'the desktop shell is not connected' } }) })
    expect(bridge.shown).toEqual([])
  })

  it('ignores WS frames that are not notifications', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'chat_message', data: note({ native: { deliver: true, reason: '' } }) })
    deliver!({ type: 'refresh', data: { kinds: ['notifications'] } })
    expect(bridge.shown).toEqual([])
  })

  it('does nothing in a browser tab', () => {
    const navigate = vi.fn()
    expect(() => {
      renderHook(() => useNativeNotifications(navigate))
      deliver!({ type: 'notification', data: note({ native: { deliver: true, reason: '' } }) })
    }).not.toThrow()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('navigates when a banner is tapped', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    bridge.tap({ route: 'inbox' })
    expect(navigate).toHaveBeenCalledWith('inbox')
  })

  it('drops a tap route it does not recognize', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    for (const route of ['', 'not-a-route', 'https://evil.example'])
      bridge.tap({ route })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('unsubscribes from taps on unmount', () => {
    const bridge = fakeBridge()
    const { unmount } = renderHook(() => useNativeNotifications(vi.fn()))
    unmount()
    expect(bridge.unsubscribedCount()).toBe(1)
  })
})
