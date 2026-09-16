
import { describe, expect, it, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { runtime } from '../../theme/runtime'
import { LiquidShape as FromBarrel } from './index'
import { LiquidShape } from './LiquidShape'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

const svg = () => document.querySelector<SVGSVGElement>('svg[data-liquid-shape]')!
const path = () => document.querySelector<SVGPathElement>('svg[data-liquid-shape] path')!
const d = () => path().getAttribute('d') ?? ''

describe('LiquidShape is reachable', () => {
  it('is exported from the ui/motion barrel', () => {
    expect(FromBarrel).toBe(LiquidShape)
  })
})

describe('the silhouette', () => {
  it('renders a real closed path, not an empty shell', () => {
    render(<LiquidShape from="circle" to="blob" active={false} />)
    expect(svg()).toBeInTheDocument()
    expect(d()).toMatch(/^M[\d.]/)
    expect(d().endsWith('Z')).toBe(true)
    expect(d().match(/C/g)).toHaveLength(16)
  })

  it('differs between the two shape states', () => {
    const { unmount } = render(<LiquidShape from="circle" to="blob" active={false} />)
    const resting = d()
    unmount()
    render(<LiquidShape from="circle" to="blob" active />)
    expect(d()).not.toBe(resting)
  })

  it('gives each named shape its own silhouette', () => {
    const shapes = ['circle', 'squircle', 'blob'] as const
    const seen = new Set<string>()
    for (const s of shapes) {
      const { unmount } = render(<LiquidShape from="circle" to={s} active />)
      seen.add(d())
      unmount()
    }
    expect(seen.size).toBe(shapes.length)
  })
})

describe('expr() scales the amplitude', () => {
  function silhouette(shape: 'circle' | 'blob', expressiveness: number, intensity = 1): number[] {
    runtime.expressiveness = expressiveness
    const { unmount } = render(<LiquidShape from={shape} to={shape} active intensity={intensity} />)
    const nums = (d().match(/-?\d+\.\d+/g) ?? []).map(Number)
    unmount()
    return nums
  }

  function amplitudeAt(expressiveness: number, intensity = 1): number {
    const blob = silhouette('blob', expressiveness, intensity)
    const circle = silhouette('circle', expressiveness, intensity)
    expect(blob.length).toBeGreaterThan(50)
    expect(blob).toHaveLength(circle.length)
    return Math.max(...blob.map((v, i) => Math.abs(v - circle[i])))
  }

  it('the amplitude tracks the expressiveness knob', () => {
    const bold = amplitudeAt(1)
    const mid = amplitudeAt(0.5)
    const refined = amplitudeAt(0)
    expect(bold).toBeGreaterThan(mid)
    expect(mid).toBeGreaterThan(refined)
  })

  it('refined is quieter but NOT dead — that is what expr()s floor is for', () => {
    const refined = amplitudeAt(0)
    expect(refined).toBeGreaterThan(1)
    expect(refined / amplitudeAt(1)).toBeCloseTo(0.35, 2)
  })

  it('scales with `intensity` too, so a call site can be quieter than the knob', () => {
    expect(amplitudeAt(1, 1)).toBeGreaterThan(amplitudeAt(1, 0.2))
  })
})

describe('the exprHeavy tier is visible in the DOM', () => {
  it('takes the bold tier above the gate', () => {
    runtime.expressiveness = 0.8
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-shape', 'morph')
    expect(svg()).toHaveAttribute('data-liquid-tier', 'bold')
  })

  it('drops to the refined tier below the gate', () => {
    runtime.expressiveness = 0.4
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-tier', 'refined')
  })
})

describe('it is decoration, and says so', () => {
  it('is aria-hidden, unfocusable and pointer-transparent', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('aria-hidden', 'true')
    expect(svg()).toHaveAttribute('focusable', 'false')
    expect(svg().style.pointerEvents).toBe('none')
  })

  it('tints from a theme var, never a literal color', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    const stops = [...document.querySelectorAll('radialGradient stop')]
    expect(stops).toHaveLength(2)
    for (const s of stops) expect(s.getAttribute('stop-color')).toBe('var(--color-primary)')
  })

  it('points its fill at its OWN gradient id', () => {
    render(<><LiquidShape from="circle" to="blob" active /><LiquidShape from="circle" to="blob" active /></>)
    const svgs = [...document.querySelectorAll('svg[data-liquid-shape]')]
    expect(svgs).toHaveLength(2)
    const ids = new Set<string>()
    for (const s of svgs) {
      const id = s.querySelector('radialGradient')!.getAttribute('id')!
      expect(s.querySelector('path')!.getAttribute('fill')).toBe(`url(#${id})`)
      expect(id).toMatch(/^liquid-[a-zA-Z0-9_-]+$/)
      ids.add(id)
    }
    expect(ids.size).toBe(2)
  })
})
