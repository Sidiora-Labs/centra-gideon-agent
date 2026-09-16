
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'

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

const { QuietButton } = await import('./QuietButton')
const { TileButton } = await import('./TileButton')
const { AddItemButton } = await import('./AddItemButton')

async function pressTransformOf(el: HTMLElement): Promise<string> {
  await act(async () => { fireEvent.pointerDown(el, { button: 0, isPrimary: true }) })
  await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
  return el.getAttribute('style') ?? ''
}

describe('under prefers-reduced-motion the press does not move the button', () => {
  const cases: [string, (onClick: () => void) => void][] = [
    ['QuietButton', (onClick) => { render(<QuietButton onClick={onClick}>Download</QuietButton>) }],
    ['TileButton', (onClick) => { render(<TileButton onClick={onClick} ariaLabel="Download">tile body</TileButton>) }],
    ['AddItemButton', (onClick) => { render(<AddItemButton onClick={onClick}>Download</AddItemButton>) }],
  ]

  for (const [name, mount] of cases) {
    it(`${name} writes no scale`, async () => {
      const onClick = vi.fn()
      mount(onClick)
      const el = screen.getByRole('button', { name: 'Download' })
      const style = await pressTransformOf(el)
      expect(/scale\(0\./.test(style), `${name} still shrinks on press: ${style}`).toBe(false)

      el.click()
      expect(onClick, `${name} stopped working under reduced motion`).toHaveBeenCalledTimes(1)
    })
  }

  it('the stub is actually in force (this file is not measuring the allowed case)', () => {
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true)
  })
})
