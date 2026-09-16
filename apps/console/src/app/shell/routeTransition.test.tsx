import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useHashRoute } from './useHashRoute'


type Svt = (cb: () => void) => unknown

const NEVER = new Promise<void>(() => {})
const hangingTransition = { ready: NEVER, finished: NEVER, updateCallbackDone: NEVER, skipTransition: () => {} }

function installViewTransition(impl: Svt | undefined): void {
  if (impl) Object.defineProperty(document, 'startViewTransition', { configurable: true, writable: true, value: impl })
  else Reflect.deleteProperty(document, 'startViewTransition')
}

function workingViewTransition() {
  return vi.fn((cb: () => void) => { cb(); return hangingTransition })
}

const ORIGINAL_MATCH_MEDIA = window.matchMedia

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

beforeEach(() => {
  history.replaceState(null, '', '#/dashboard')
})

afterEach(() => {
  installViewTransition(undefined)
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  vi.restoreAllMocks()
})

function mount() {
  return renderHook(() => useHashRoute('dashboard'))
}

function RouteProbe() {
  const { route } = useHashRoute('dashboard')
  return <span data-testid="route">{route}</span>
}

function hashChangeTo(hash: string): void {
  act(() => {
    history.replaceState(null, '', hash)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

function navigate(nav: () => void): void {
  act(() => {
    nav()
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('route transitions', () => {
  it('crossfades a route change, and commits the route through the transition', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(started).toHaveBeenCalledTimes(1)
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })


  it('navigates when the platform has no View Transitions API', () => {
    expect(document.startViewTransition).toBeUndefined()
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates when startViewTransition THROWS', () => {
    installViewTransition(() => { throw new Error('transition refused') })
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates when the transition NEVER SETTLES', () => {
    installViewTransition((cb) => { cb(); return hangingTransition })
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates under reduced motion with no transition at all', () => {
    setReducedMotion(true)
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(started).not.toHaveBeenCalled()
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('crossfades browser back/forward, which no page opts into', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    hashChangeTo('#/agents')

    expect(started).toHaveBeenCalledTimes(1)
    expect(result.current.route).toBe('agents')
  })

  it('crossfades EVERY route change, including returning to the one it started on', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    hashChangeTo('#/agents')
    expect(result.current.route).toBe('agents')
    hashChangeTo('#/dashboard')

    expect(result.current.route).toBe('dashboard')
    expect(started).toHaveBeenCalledTimes(2)
  })

  it('commits the new DOM BEFORE the transition captures it', () => {
    let domWhenCaptured = ''
    installViewTransition((cb) => {
      cb()
      domWhenCaptured = screen.getByTestId('route').textContent ?? ''
      return hangingTransition
    })
    render(<RouteProbe />)

    hashChangeTo('#/agents')

    expect(domWhenCaptured).toBe('agents')
  })


  it('does not crossfade a PUSHED query change, which reaches the same hashchange seam', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.setQuery({ open: 'task-1' }))

    expect(started).not.toHaveBeenCalled()
    expect(result.current.query.open).toBe('task-1')
    expect(result.current.route).toBe('dashboard')
  })

  it('does not crossfade a replaced in-place refinement, and still applies it', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    act(() => { result.current.setQuery({ q: 'ship' }, { replace: true }) })

    expect(started).not.toHaveBeenCalled()
    expect(result.current.query.q).toBe('ship')
    expect(result.current.route).toBe('dashboard')
  })

  it('does not crossfade a replace navigation — a URL correction is not a navigation', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    act(() => { result.current.navigate('agents', { replace: true }) })

    expect(started).not.toHaveBeenCalled()
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })


  it('leaves navEpoch semantics alone: a path nav bumps it, a query update does not', () => {
    installViewTransition(workingViewTransition())
    const { result } = mount()
    const before = result.current.navEpoch

    navigate(() => result.current.navigate('agents'))
    const afterNav = result.current.navEpoch
    expect(afterNav).toBeGreaterThan(before)

    act(() => { result.current.setQuery({ tab: 'runs' }, { replace: true }) })
    expect(result.current.navEpoch).toBe(afterNav)
  })

  it('still parses sub-path and query across an animated navigation', () => {
    installViewTransition(workingViewTransition())
    const { result } = mount()

    navigate(() => result.current.navigate('chat/abc-1?tab=files'))

    expect(result.current.route).toBe('chat')
    expect(result.current.sub).toBe('abc-1')
    expect(result.current.query.tab).toBe('files')
  })
})

describe('the route crossfade curve', () => {
  it('is declared on the app curve in tokens.css, not left to the UA default', () => {
    const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
    const rule = /::view-transition-old\(root\)[^{]*\{([^}]*)\}/.exec(css)
    expect(rule, '::view-transition-old(root) has no rule in tokens.css').toBeTruthy()
    expect(rule![1]).toContain('var(--ease-emphasized-decel)')
    expect(css).toContain('::view-transition-new(root)')
  })
})

describe('navigation controller state', () => {
  it('keeps callback identities stable across route and query changes', () => {
    const { result } = mount()
    const navigateFirst = result.current.navigate
    const queryFirst = result.current.setQuery
    navigate(() => result.current.navigate('chat/session'))
    act(() => result.current.setQuery({ tab: 'files' }, { replace: true }))
    expect(result.current.navigate).toBe(navigateFirst)
    expect(result.current.setQuery).toBe(queryFirst)
  })

  it('preserves encoded path separators while editing the query', () => {
    const { result } = mount()
    navigate(() => result.current.navigate('chat/session%2Fwith%3Freserved'))
    act(() => result.current.setQuery({ q: 'changed' }, { replace: true }))
    expect(location.hash).toBe('#/chat/session%2Fwith%3Freserved?q=changed')
    expect(result.current.sub).toBe('session/with?reserved')
  })
})
