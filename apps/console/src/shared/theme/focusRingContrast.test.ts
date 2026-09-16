import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'


const lum = (hex: string): number => {
  const h = hex.replace('#', '')
  const ch = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4)
}
const contrast = (a: string, b: string): number => {
  const la = lum(a), lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
const overlay = (hex: string, base: string, alpha: number): string => {
  const p = (h: string, i: number) => parseInt(h.replace('#', '').slice(i, i + 2), 16)
  return '#' + [0, 2, 4]
    .map((i) => Math.round(p(hex, i) * alpha + p(base, i) * (1 - alpha)))
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')
}

function tier(mode: 'dark' | 'light', name: string): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const scope = mode === 'dark' ? css : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{3,8})`))
  if (!m) throw new Error(`no --color-${name} for ${mode}`)
  return m[1]
}

const TIERS = ['surface', 'surface-low', 'surface-container', 'surface-high', 'surface-highest', 'canvas']

const FLOOR = 3

function primaryOf(s: (typeof SCHEMES)[number], mode: 'dark' | 'light'): string {
  const v = s.colors['--color-primary'] as unknown as { dark?: string; light: string } | string
  const hex = typeof v === 'string' ? v : (mode === 'dark' ? (v.dark ?? v.light) : v.light)
  if (typeof hex !== 'string' || !hex.startsWith('#')) throw new Error(`unparsed primary for ${s.id}/${mode}`)
  return hex
}

describe('the focus indicator clears the 3:1 floor in every scheme, mode and surface', () => {
  it('has the full curated scheme set — the sweep is not measuring a subset', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  it('the floor is the standard, not a local preference', () => {
    expect(FLOOR, 'SC 1.4.11 / SC 2.4.11 set 3:1 for a UI component boundary — not tunable').toBe(3)
  })

  it('opaque primary is >= 3:1 on every surface a ring can sit on', () => {
    const failures: string[] = []
    let worst = { ratio: Infinity, where: '' }
    for (const s of SCHEMES) {
      for (const mode of ['dark', 'light'] as const) {
        const prim = primaryOf(s, mode)
        for (const t of TIERS) {
          const base = tier(mode, t)
          const r = contrast(prim, base)
          if (r < worst.ratio) worst = { ratio: r, where: `${s.id}/${mode}/${t}` }
          if (r < FLOOR) failures.push(`${s.id}/${mode}/${t}: ${r.toFixed(2)}:1 (${prim} on ${base})`)
        }
      }
    }
    expect(failures, `a focus ring nobody can see:\n${failures.join('\n')}`).toEqual([])
    expect(worst.ratio, `worst case is ${worst.where} at ${worst.ratio.toFixed(2)}:1`)
      .toBeGreaterThanOrEqual(3.5)
  })

  it('and the 50% tint it replaced could NOT have passed — the reason, as an assertion', () => {
    let worst = Infinity
    for (const s of SCHEMES) {
      for (const mode of ['dark', 'light'] as const) {
        const prim = primaryOf(s, mode)
        for (const t of TIERS) {
          const base = tier(mode, t)
          worst = Math.min(worst, contrast(overlay(prim, base, 0.5), base))
        }
      }
    }
    expect(worst, 'a 50% tint of the accent cannot reach 3:1 against its own surface')
      .toBeLessThan(FLOOR)
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    return statSync(p).isDirectory() ? walk(p) : (/\.(tsx?|css)$/.test(n) ? [p] : [])
  })

const ALPHA_FOCUS_RING = /[\w[\]:>-]*focus[\w[\]:>-]*:ring-primary\/\d+/

describe('no focus ring may carry an alpha', () => {
  it('nowhere in src', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      if (abs.endsWith('focusRingContrast.test.ts')) continue
      const src = readFileSync(abs, 'utf8')
      for (const m of src.matchAll(new RegExp(ALPHA_FOCUS_RING, 'g'))) {
        offenders.push(`${abs.slice(SRC.length + 1)}: ${m[0]}`)
      }
    }
    expect(offenders, `a translucent focus ring measures 1.89-2.44:1 — use ring-primary:\n${offenders.join('\n')}`)
      .toEqual([])
  })

  it('finds the population it guards (not vacuously green)', () => {
    const uses = walk(SRC).filter((f) => /(focus|focus-visible|focus-within):ring-primary\b/.test(readFileSync(f, 'utf8')))
    expect(uses.length, 'the app must still install its own focus ring').toBeGreaterThanOrEqual(66)
  })

  it('is scoped to focus states, and only to them', () => {
    expect(ALPHA_FOCUS_RING.test('focus:ring-primary/50')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('focus-within:ring-primary/50')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('focus-visible:ring-primary/40')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('has-[input:focus-visible]:ring-primary/50'),
      'the picker idiom — the prefix a narrower guard missed').toBe(true)
    expect(ALPHA_FOCUS_RING.test('has-[>button:focus-visible]:ring-primary/50'),
      'the list-row overlay idiom').toBe(true)
    expect(ALPHA_FOCUS_RING.test('ring-1 ring-primary/30'), 'a decorative tint is not an indicator').toBe(false)
    expect(ALPHA_FOCUS_RING.test('focus:ring-primary'), 'the compliant spelling must pass').toBe(false)
  })
})
