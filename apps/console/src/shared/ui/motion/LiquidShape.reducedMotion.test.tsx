
import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

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

const { LiquidShape } = await import('./LiquidShape')

const svg = () => document.querySelector<SVGSVGElement>('svg[data-liquid-shape]')!
const path = () => document.querySelector<SVGPathElement>('svg[data-liquid-shape] path')!
const d = () => path().getAttribute('d') ?? ''

describe('under prefers-reduced-motion the shape just IS', () => {
  it('takes the instant branch, and the surface is still drawn', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-shape', 'instant')
    expect(svg()).toHaveAttribute('data-liquid-tier', 'reduced')
    expect(d()).toMatch(/^M[\d.]/)
    expect(d().match(/C/g)).toHaveLength(16)
  })

  it('changes state synchronously — instant, not merely quick', () => {
    const { rerender } = render(<LiquidShape from="circle" to="blob" active={false} />)
    const resting = d()
    expect(resting).toMatch(/^M[\d.]/)
    rerender(<LiquidShape from="circle" to="blob" active />)
    expect(d()).not.toBe(resting)
  })

  it('never runs the idle breathe — the silhouette does not drift', async () => {
    vi.useRealTimers()
    render(<LiquidShape from="circle" to="squircle" active />)
    const first = d()
    expect(first).toMatch(/^M[\d.]/)
    await new Promise((r) => setTimeout(r, 120))
    expect(d()).toBe(first)
  })
})
