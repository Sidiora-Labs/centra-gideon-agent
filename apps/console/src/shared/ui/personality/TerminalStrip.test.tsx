
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { TerminalStrip } from './TerminalStrip'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

const listeners = new Set<() => void>()
let reduceMatches = false

function defineMatchMedia() {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return query.includes('prefers-reduced-motion') ? reduceMatches : false
      },
      media: query,
      addEventListener: (_: string, fn: () => void) => { listeners.add(fn) },
      removeEventListener: (_: string, fn: () => void) => { listeners.delete(fn) },
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }) as unknown as MediaQueryList,
  })
}

defineMatchMedia()
reduceMatches = false

beforeEach(() => {
  listeners.clear()
  reduceMatches = false
})

afterAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA,
  })
})

const root = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-shell-element="terminal-scanlines"]')!
const beam = (c: HTMLElement) => c.querySelector<HTMLElement>('.crt-beam')

describe('with motion allowed the raster animates', () => {
  it('renders the travelling beam alongside the static raster', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container).className).toContain('crt-raster')
    expect(beam(container), 'the beam must render when motion is allowed').not.toBeNull()
  })

  it('is invisible to assistive tech and to the pointer', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container).getAttribute('aria-hidden')).toBe('true')
    expect(root(container).className).toContain('pointer-events-none')
  })

  it('sits above page content and below the surfaces a user must act on', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container).className).toContain('z-[var(--z-overlay)]')
  })

  it('goes static the moment the OS preference flips, with no reload', () => {
    const { container } = render(<TerminalStrip />)
    expect(beam(container)).not.toBeNull()

    reduceMatches = true
    act(() => { for (const fn of listeners) fn() })
    expect(beam(container), 'flipping to reduce must drop the beam').toBeNull()
    expect(root(container).className, 'the static raster must survive').toContain('crt-raster')

    reduceMatches = false
    act(() => { for (const fn of listeners) fn() })
    expect(beam(container), 'flipping back must restore the beam').not.toBeNull()
  })

  it('unsubscribes on unmount', () => {
    const { unmount } = render(<TerminalStrip />)
    expect(listeners.size).toBe(1)
    unmount()
    expect(listeners.size).toBe(0)
  })

  it('survives a host with no matchMedia at all', () => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: undefined })
    try {
      const { container } = render(<TerminalStrip />)
      expect(root(container)).not.toBeNull()
      expect(beam(container), 'no query to consult → motion allowed').not.toBeNull()
    } finally {
      defineMatchMedia()
    }
  })
})

it('the stub is actually the thing being consulted', () => {
  const spy = vi.spyOn(window, 'matchMedia')
  render(<TerminalStrip />)
  expect(spy).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  spy.mockRestore()
})
