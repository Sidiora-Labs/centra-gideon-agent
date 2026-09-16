import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('DegradedChip is dismissible from the keyboard', () => {
  const src = read('shared/ui/DegradedChip.tsx')

  it('Escape closes it and returns focus to the chip', () => {
    expect(src).toMatch(/e\.key !== 'Escape'/)
    expect(src).toMatch(/setOpen\(false\)/)
    expect(src).toMatch(/triggerRef\.current\?\.focus\(\)/)
  })

  it('the trigger carries the ref that focus returns to', () => {
    expect(src).toMatch(/<button ref=\{triggerRef\}/)
  })

  it('Escape is consumed so one press does not close two layers', () => {
    expect(src).toMatch(/e\.stopPropagation\(\)/)
  })

  it('the listener is scoped to the open state', () => {
    expect(src).toMatch(/if \(!open\) return/)
  })
})

describe('the NavRail overlay drawer is dismissible from the keyboard', () => {
  const src = read('app/shell/App.tsx')

  it('Escape closes the drawer', () => {
    expect(src).toMatch(/if \(!mobileNavOpen\) return/)
    expect(src).toMatch(/setMobileNavOpen\(false\)/)
    expect(src).toMatch(/e\.stopPropagation\(\)/)
  })

  it('the drawer is reachable at desktop widths, which is why it needs Escape', () => {
    expect(read('app/shell/useIsMobile.ts')).toMatch(/max-width: 768px/)
    expect(/navigator\.maxTouchPoints|ontouchstart/.test(read('app/shell/useIsMobile.ts'))).toBe(false)
  })
})

describe('the rail: an overlay with a click-away scrim also binds Escape', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  const SCRIM = /className="[^"]*\b(?:fixed|absolute)\b[^"]*\binset-0\b[^"]*"[^>]{0,140}onClick=/
  const withScrim = files.filter((f) => SCRIM.test(f.src))

  it('every file with a click-away scrim handles Escape', () => {
    const offenders = withScrim.filter((f) => !/'Escape'/.test(f.src)).map((f) => f.rel)
    expect(
      offenders,
      `A scrim is a MOUSE dismissal; without an Escape handler a keyboard user cannot close the ` +
        `overlay:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the scrim-bearing files', () => {
    expect(withScrim.length, 'the scanner must find the scrim-bearing overlays').toBeGreaterThanOrEqual(10)
    const rels = withScrim.map((f) => f.rel)
    expect(rels).toContain('shared/ui/DegradedChip.tsx')
    expect(rels).toContain('shared/ui/Modal.tsx')
    for (const cls of ['fixed inset-0 z-40', 'absolute inset-0 bg-canvas/70']) {
      expect(SCRIM.test(`<div className="${cls}" onClick={close} />`), cls).toBe(true)
    }
    expect(SCRIM.test('<div className="fixed inset-0 z-[100] overflow-hidden" style={{}}>')).toBe(false)
    const sample = { rel: 'x.tsx', src: '<div className="fixed inset-0 z-40" onClick={close} />' }
    expect(SCRIM.test(sample.src) && !/'Escape'/.test(sample.src)).toBe(true)
  })
})
