
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { TerminalStrip } from './TerminalStrip'

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

const root = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-shell-element="terminal-scanlines"]')!

describe('under prefers-reduced-motion the raster is a static frame', () => {
  it('renders NO travelling beam', () => {
    const { container } = render(<TerminalStrip />)
    expect(container.querySelector('.crt-beam'), 'the beam must not render').toBeNull()
  })

  it('still renders the static raster — this is a frozen frame, not a blank layer', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container), 'the shell element must still mount').not.toBeNull()
    expect(root(container).className).toContain('crt-raster')
  })

  it('keeps the decorative contract', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container).getAttribute('aria-hidden')).toBe('true')
    expect(root(container).className).toContain('pointer-events-none')
  })

  it('no node in the subtree carries an animation class', () => {
    const { container } = render(<TerminalStrip />)
    const animated = [...container.querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\bcrt-beam\b|\banimation:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.className), 'these nodes still animate').toEqual([])
  })
})
