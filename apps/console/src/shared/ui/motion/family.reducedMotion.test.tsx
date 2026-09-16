
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

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

const { Bud, Disintegrate, LiquidShape, Morph, MORPH_FAMILY, familySpring } = await import('./index')
const { instant } = await import('../../theme/motion')

describe('every family member takes an instant branch, and says so in the DOM', () => {
  it('Morph drops the shared element', () => {
    render(<Morph id="artifact-x"><p>card</p></Morph>)
    expect(document.querySelector('[data-morph]')).toHaveAttribute('data-morph', 'none')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('Bud drops the squish — no transform, no projection node, children intact', () => {
    const onPoke = vi.fn()
    render(<Bud from="top" className="p-2"><button type="button" onClick={onPoke}>pick</button></Bud>)
    const el = document.querySelector<HTMLElement>('[data-bud]')!
    expect(el).toHaveAttribute('data-bud', 'instant')
    expect(el.tagName).toBe('DIV')
    expect(el.getAttribute('style')).toBeNull()
    expect(el.style.transform).toBe('')
    expect(el.style.willChange).toBe('')
    expect(el.className).toBe('p-2')
    screen.getByRole('button', { name: 'pick' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('LiquidShape renders the target silhouette directly', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    const svg = document.querySelector('svg[data-liquid-shape]')!
    expect(svg).toHaveAttribute('data-liquid-shape', 'instant')
    expect(svg).toHaveAttribute('data-liquid-tier', 'reduced')
    expect(svg.querySelector('path')!.getAttribute('d')).toMatch(/^M[\d.]/)
  })

  it('Disintegrate resolves with no animation at all', async () => {
    const onDone = vi.fn()
    const { rerender } = render(
      <Disintegrate active={false} onDone={onDone}><p>row</p></Disintegrate>,
    )
    expect(screen.getByText('row')).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()

    rerender(<Disintegrate active onDone={onDone}><p>row</p></Disintegrate>)
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('row')).not.toBeInTheDocument()
    expect(document.querySelector('.pointer-events-none')).toBeNull()
  })
})

describe('the shared spring collapses, and collapses CLEANLY', () => {
  it('returns `instant` untouched for every base — no spring residue', () => {
    for (const base of [MORPH_FAMILY.flight, MORPH_FAMILY.state, MORPH_FAMILY.spawn]) {
      const t = familySpring(base) as Record<string, unknown>
      expect(t).toEqual(instant)
      expect(t.type).toBe('tween')
      expect(t.duration).toBe(0)
      expect(t.stiffness).toBeUndefined()
      expect(t.damping).toBeUndefined()
    }
  })

  it('is INSTANT, not merely quick — and that is a different state from expressiveness 0', async () => {
    const { runtime } = await import('../../theme/runtime')
    const before = runtime.expressiveness
    try {
      runtime.expressiveness = 1
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
      runtime.expressiveness = 0
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
    } finally {
      runtime.expressiveness = before
    }
  })
})
