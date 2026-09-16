
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

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

const { Morph } = await import('./Morph')
const { MORPH_FAMILY, familySpring } = await import('./vocabulary')
const { instant } = await import('../../theme/motion')

const root = () => document.querySelector<HTMLElement>('[data-morph]')!

describe('under prefers-reduced-motion the card just IS the page', () => {
  it('takes the no-morph branch — no shared element at either end', () => {
    render(<><Morph id="artifact-x" className="grid"><p>card</p></Morph></>)
    expect(root()).toHaveAttribute('data-morph', 'none')
    expect(root().tagName).toBe('DIV')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('writes no transform, no opacity and no will-change on the first commit', () => {
    render(<Morph id="artifact-x"><p>card</p></Morph>)
    expect(root().style.transform).toBe('')
    expect(root().style.opacity).toBe('')
    expect(root().style.willChange).toBe('')
    expect(root().getAttribute('style')).toBeNull()
  })

  it('the transition has NO spring residue, even though the preset was spread', () => {
    const t = familySpring(MORPH_FAMILY.flight) as Record<string, unknown>
    expect(t).toEqual(instant)
    expect(t.type).toBe('tween')
    expect(t.duration).toBe(0)
    expect(t.stiffness).toBeUndefined()
    expect(t.damping).toBeUndefined()
  })

  it('still renders the card and its control still works', () => {
    const onPoke = vi.fn()
    render(
      <Morph id="artifact-x" className="grid" style={{ minWidth: 0 }}>
        <button type="button" onClick={onPoke}>open</button>
      </Morph>,
    )
    expect(root().className).toBe('grid')
    expect(root().style.minWidth).toBe('0px')
    screen.getByRole('button', { name: 'open' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })
})
