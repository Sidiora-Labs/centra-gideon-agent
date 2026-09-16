import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SecurityPanel } from './SecurityPanel'
import type { DesktopStateWire } from '../../shared/data/api'
import type { LoginItemResult, LoginItemState } from '../../shared/data/desktopBridge'

const STATS = { denied_commands: 0, suspicious_patterns: 0, tool_schemas: 0, redaction_paths: 0 }
const desktopState = vi.fn<() => Promise<DesktopStateWire | null>>()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      securityStats: () => Promise.resolve(STATS),
      deniedCommands: () => Promise.resolve({
        builtin: [], user: [],
        baseline: { version: 1, sha256: '0'.repeat(64), count: 0, verified: true, detail: '' },
        user_additions: 0,
      }),
      securityEgress: () => Promise.reject(new Error('not under test')),
      desktopState: () => desktopState(),
    },
  }
})

const DESCRIBES = 'macOS Login Items (System Settings → General → Login Items) for this app bundle only'
const UNSUPPORTED = 'login items are not implemented on linux'

const CONNECTED: DesktopStateWire = {
  connected: true,
  shell: { version: '0.1.0', platform: 'darwin' },
  capabilities: {
    login_item: { available: true, granted: 'granted', requestable: false, reason: '' },
  },
  registered_at: '2026-08-27T00:00:00+00:00',
  last_seen: '2026-08-27T00:00:00+00:00',
}

function fakeLoginItemBridge({
  enabled = false,
  supported = true,
  refuse = false,
  absent = false,
} = {}) {
  let current = enabled
  const sets: boolean[] = []
  const bridge: Record<string, unknown> = {
    capabilities: {
      names: () => [], probe: vi.fn(), snapshot: vi.fn(), request: vi.fn(), on: () => () => {},
    },
  }
  if (!absent) {
    bridge.loginItem = {
      get: (): Promise<LoginItemState> =>
        Promise.resolve({ enabled: current, supported, describes: supported ? DESCRIBES : UNSUPPORTED }),
      set: (next: boolean): Promise<LoginItemResult> => {
        sets.push(next)
        if (!supported) {
          return Promise.resolve({ ok: false, enabled: false, changed: false, supported: false, reason: 'login items are not implemented on linux' })
        }
        if (refuse) {
          return Promise.resolve({ ok: false, enabled: current, changed: false, supported: true, reason: 'the OS did not apply the change' })
        }
        const changed = current !== next
        current = next
        return Promise.resolve({ ok: true, enabled: current, changed, supported: true })
      },
    }
  }
  ;(window as unknown as { gideonDesktop: unknown }).gideonDesktop = bridge
  return { sets, current: () => current }
}

const toggle = () => screen.getByRole('switch', { name: /open at login/i })

beforeEach(() => {
  sessionStorage.clear()
  desktopState.mockReset()
  desktopState.mockResolvedValue(CONNECTED)
})

afterEach(() => {
  delete window.gideonDesktop
})

describe('Open at login — the desktop-only gate', () => {
  it('renders NO toggle in a browser tab', async () => {
    desktopState.mockResolvedValue({
      connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
    })
    render(<SecurityPanel />)
    expect(await screen.findByText(/Desktop app not connected/)).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })

  it('renders no toggle when the gateway reports a shell but this page has no bridge', async () => {
    render(<SecurityPanel />)
    expect(await screen.findByText('Open at login')).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })

  it('falls back to the plain capability row on a shell with no loginItem namespace', async () => {
    fakeLoginItemBridge({ absent: true })
    render(<SecurityPanel />)
    expect(await screen.findByText('Open at login')).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })
})

describe('Open at login — inside the desktop shell', () => {
  it('reports the OS registration, not the capability probe', async () => {
    fakeLoginItemBridge({ enabled: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    expect(screen.getByText(/starts only when you open it/)).toBeTruthy()
    expect(screen.queryByText('Granted')).toBeNull()
  })

  it('names what it touches before the user flips it', async () => {
    fakeLoginItemBridge()
    render(<SecurityPanel />)
    expect(await screen.findByText(DESCRIBES)).toBeTruthy()
  })

  it('reflects an already-registered login item', async () => {
    fakeLoginItemBridge({ enabled: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
    expect(screen.getByText(/starts when you log in/)).toBeTruthy()
  })

  it('WRITES THROUGH THE BRIDGE when flipped, and shows what came back', async () => {
    const b = fakeLoginItemBridge({ enabled: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())

    await userEvent.click(toggle())

    await waitFor(() => expect(b.sets).toEqual([true]))
    expect(b.current()).toBe(true)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
  })

  it('un-registers through the same call', async () => {
    const b = fakeLoginItemBridge({ enabled: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))

    await userEvent.click(toggle())

    await waitFor(() => expect(b.sets).toEqual([false]))
    expect(b.current()).toBe(false)
  })

  it('a refused write leaves the switch showing the OS, not the request', async () => {
    const b = fakeLoginItemBridge({ enabled: false, refuse: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())

    await userEvent.click(toggle())

    await waitFor(() => expect(screen.getByText(/did not apply the change/)).toBeTruthy())
    expect(b.sets).toEqual([true])
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })

  it('an unsupported platform softens the switch and NAMES why, keeping it reachable', async () => {
    fakeLoginItemBridge({ supported: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    expect(toggle().getAttribute('aria-disabled')).toBe('true')
    expect(toggle()).not.toBeDisabled()
    expect(toggle().getAttribute('title')).toBe(UNSUPPORTED)
  })

  it('an unsupported platform cannot write a registration the OS has no API for', async () => {
    const b = fakeLoginItemBridge({ supported: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    await userEvent.click(toggle())
    expect(b.sets).toEqual([])
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })
})
