import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ProgressRing } from './ProgressRing'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

function arcOf(container: HTMLElement): SVGCircleElement {
  const circles = container.querySelectorAll('circle')
  expect(circles.length, 'a track circle and an arc circle').toBe(2)
  return circles[1] as SVGCircleElement
}

describe('ProgressRing geometry', () => {
  it('renders a track and an arc with the shared geometry', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.5} tone="var(--color-primary)" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('width')).toBe('28')
    expect(svg.getAttribute('viewBox')).toBe('0 0 28 28')

    const circles = container.querySelectorAll('circle')
    expect(circles[0].getAttribute('r')).toBe('11.5')
    expect(circles[0].getAttribute('stroke')).toBe('var(--color-surface-high)')
    expect(circles[0].getAttribute('stroke-width')).toBe('2.5')
  })

  it('starts the arc at 12 o_clock and takes the caller tone', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.25} tone="var(--color-warn)" />)
    const arc = arcOf(container as HTMLElement)
    expect(arc.getAttribute('stroke')).toBe('var(--color-warn)')
    expect(arc.getAttribute('stroke-linecap')).toBe('round')
    expect(arc.getAttribute('transform')).toBe('rotate(-90 14 14)')
  })

  it('honours size, and scales the radius with it', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={1} tone="red" size={40} />)
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('40')
    expect(container.querySelector('circle')!.getAttribute('r')).toBe('17.5')
  })

  it('takes pct as a FRACTION: the dash array is the full circumference', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.5} tone="red" />)
    const c = 2 * Math.PI * 11.5
    expect(Number(arcOf(container as HTMLElement).getAttribute('stroke-dasharray'))).toBeCloseTo(c, 3)
  })
})

describe('the divergence this primitive resolves', () => {
  it('the arc is a motion element with a spring — not a plain circle', () => {
    const src = read('shared/ui/ProgressRing.tsx')
    expect(src).toMatch(/<motion\.circle/)
    expect(src).toMatch(/animate=\{\{ strokeDashoffset: offset \}\}/)
    expect(src).toMatch(/spring\.spatialSlow/)
  })

  it('mount is silent — initial={false}, so a list does not sweep every arc from zero', () => {
    expect(read('shared/ui/ProgressRing.tsx')).toMatch(/initial=\{false\}/)
  })

  it('reduced motion sets the arc directly instead of easing it', () => {
    const src = read('shared/ui/ProgressRing.tsx')
    expect(src).toMatch(/useReducedMotion/)
    expect(src).toMatch(/reduce \? \{ duration: 0 \} : spring\.spatialSlow/)
  })

  it('neither page declares its own ProgressRing any more', () => {
    for (const rel of ['features/dashboard/widgets/ActiveWork.tsx', 'features/loops/LoopsListPage.tsx']) {
      const src = read(rel)
      expect(/function ProgressRing\b/.test(src), `${rel} should not declare its own ring`).toBe(false)
      expect(src).toMatch(/import \{ ProgressRing \} from '.*ui\/ProgressRing'/)
    }
  })

  it('LoopsListPage no longer sets strokeDashoffset by hand anywhere', () => {
    const code = read('features/loops/LoopsListPage.tsx')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    expect(/strokeDashoffset/.test(code)).toBe(false)
  })
})

describe('the ring announces itself, and its abbreviation has a full word', () => {

  it('is a progressbar with a value, not a bare svg', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.4} tone="red" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('progressbar')
    expect(svg.getAttribute('aria-valuemin')).toBe('0')
    expect(svg.getAttribute('aria-valuemax')).toBe('100')
    expect(svg.getAttribute('aria-valuenow')).toBe('40')
  })

  it('carries the name its caller gave it', () => {
    const { container } = render(<ProgressRing label="Cycle progress: 3 of 8" pct={0.375} tone="red" />)
    expect(container.querySelector('svg')!.getAttribute('aria-label')).toBe('Cycle progress: 3 of 8')
  })

  it('clamps the reported value the way the arc does', () => {
    const over = render(<ProgressRing label="x" pct={1.4} tone="red" />).container.querySelector('svg')!
    const under = render(<ProgressRing label="x" pct={-0.2} tone="red" />).container.querySelector('svg')!
    expect(over.getAttribute('aria-valuenow')).toBe('100')
    expect(under.getAttribute('aria-valuenow')).toBe('0')
  })

  it('every call site names what it is counting', () => {
    const SRC = join(process.cwd(), "src")
    const sites = ['features/loops/LoopsListPage.tsx', 'features/dashboard/widgets/ActiveWork.tsx']
    for (const rel of sites) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must pass a label`).toMatch(/<ProgressRing[\s\S]{0,220}?label=\{`Cycle progress/)
    }
  })

  it("the loops row's abbreviation keeps a full-word equivalent", () => {
    const src = readFileSync(join(process.cwd(), "src/features/loops/LoopsListPage.tsx"), 'utf8')
    expect(src, 'the eye keeps the abbreviation').toMatch(/aria-hidden="true">\{c\.findings\?\.length \?\? 0\} fnd</)
    expect(src, 'assistive tech gets the word').toMatch(/className="sr-only">\{c\.findings\?\.length \?\? 0\} findings</)
    expect(src, 'and a hover expansion').toMatch(/title=\{`\$\{c\.findings\?\.length \?\? 0\} findings`\}/)
  })
})
