import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DEFAULT_SCHEME, SCHEMES } from './schemes'


function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  const hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

const AA = 4.5

function darkSurfaceContainer(): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const m = css.match(/--color-surface-container:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-surface-container in tokens.css')
  return m[1]
}

function surfaceHigh(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const scope = mode === 'dark'
    ? css
    : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-surface-high:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error(`could not find --color-surface-high for ${mode}`)
  return m[1]
}

function surfaceLow(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const scope = mode === 'dark' ? css : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-surface-low:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error(`could not find --color-surface-low for ${mode}`)
  return m[1]
}

const WHITE = '#ffffff'

function lightCanvas(): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const block = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1]
  if (!block) throw new Error('could not find the .light rule block in tokens.css')
  const m = block.match(/--color-canvas:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-canvas inside the .light block')
  return m[1]
}

describe('scheme contrast: every scheme meets WCAG AA (not just the default)', () => {
  const DARK_SURFACE = darkSurfaceContainer()
  const LIGHT_CANVAS = lightCanvas()
  const HIGH_LIGHT = surfaceHigh('light')
  const HIGH_DARK = surfaceHigh('dark')
  const LOW_LIGHT = surfaceLow('light')
  const LOW_DARK = surfaceLow('dark')

  it('has the full curated scheme set', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  for (const s of SCHEMES) {
    const c = s.colors
    const primary = c['--color-primary']
    const emphasis = c['--color-primary-emphasis']
    const onPrimary = c['--color-on-primary']
    const info = c['--color-info']

    describe(`scheme '${s.id}'`, () => {
      it('light: primary as filled control (onPrimary over primary) ≥ AA', () => {
        expect(contrast(primary.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: primary-emphasis as accent text on the CANVAS ≥ AA', () => {
        expect(contrast(emphasis.light, LIGHT_CANVAS)).toBeGreaterThanOrEqual(AA)
      })

      it('light: primary-emphasis as accent text on SURFACE-HIGH ≥ AA', () => {
        expect(contrast(emphasis.light, HIGH_LIGHT)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: primary-emphasis as accent text on SURFACE-HIGH ≥ AA', () => {
        expect(contrast(emphasis.dark, HIGH_DARK)).toBeGreaterThanOrEqual(AA)
      })

      it('light: primary-emphasis as accent text on SURFACE-LOW ≥ AA', () => {
        expect(contrast(emphasis.light, LOW_LIGHT)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: primary-emphasis as accent text on SURFACE-LOW ≥ AA', () => {
        expect(contrast(emphasis.dark, LOW_DARK)).toBeGreaterThanOrEqual(AA)
      })

      it('light: primary as accent text on white ≥ AA', () => {
        expect(contrast(primary.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })
      it('light: emphasis (hover fill) over onPrimary ≥ AA', () => {
        expect(contrast(emphasis.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: info as accent text on white ≥ AA', () => {
        expect(contrast(info.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })

      it('dark: primary as accent text on surface-container ≥ AA', () => {
        expect(contrast(primary.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: info as accent text on surface-container ≥ AA', () => {
        expect(contrast(info.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})


function onPrimaryContainer(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const all = [...css.matchAll(/--color-on-primary-container:\s*(#[0-9a-fA-F]{3,8})/g)].map((m) => m[1])
  if (all.length < 2) throw new Error(`expected a dark AND a light --color-on-primary-container, found ${all.length}`)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

describe('accent container: ink on the tinted accent surface meets AA in every scheme', () => {
  const INK = { dark: onPrimaryContainer('dark'), light: onPrimaryContainer('light') }

  it('found both mode values in tokens.css (not vacuously green)', () => {
    expect(INK.dark).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.light).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.dark).not.toBe(INK.light)
  })

  for (const s of SCHEMES) {
    const container = s.colors['--color-primary-container']
    describe(`scheme '${s.id}'`, () => {
      it('light: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.light, container.light)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.dark, container.dark)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})


function tintInkDecl(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const scope = mode === 'dark'
    ? css.slice(0, css.search(/\.light\s*\{/))
    : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-on-primary-tint:\s*([^;]+);/)
  if (!m) throw new Error(`could not find --color-on-primary-tint for ${mode}`)
  return m[1].trim()
}

function tonalGrounds(mode: 'dark' | 'light'): Array<[string, string]> {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const scope = mode === 'dark' ? css.slice(0, css.search(/\.light\s*\{/)) : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  return ['--color-surface', '--color-surface-low', '--color-surface-container'].map((name) => {
    const m = scope.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))
    if (!m) throw new Error(`could not find ${name} for ${mode}`)
    return [name.replace('--color-', ''), m[1]] as [string, string]
  })
}

function tonalAlphas(): Array<[string, number]> {
  const btn = readFileSync(join(process.cwd(), "src/shared/ui/Button.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const tonal = /tonal:\s*'([^']+)'/.exec(btn)?.[1] ?? ''
  return [...tonal.matchAll(/(hover:)?bg-primary\/(\d+)\b/g)]
    .map((m) => [m[1] ? 'hover' : 'rest', Number(m[2]) / 100] as [string, number])
}

type Rgb = [number, number, number]
const toRgb = (hex: string): Rgb => {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb
}
function luminanceRgb([r, g, b]: Rgb): number {
  const chan = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4) }
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
}
function contrastRgb(a: Rgb, b: Rgb): number {
  const la = luminanceRgb(a), lb = luminanceRgb(b)
  const hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}
const composite = (fg: Rgb, bg: Rgb, a: number): Rgb => fg.map((v, i) => a * v + (1 - a) * bg[i]) as Rgb
const colorMixSrgb = (c: Rgb, p: number, other: Rgb): Rgb => c.map((v, i) => p * v + (1 - p) * other[i]) as Rgb

function resolveTintInk(decl: string, s: (typeof SCHEMES)[number], mode: 'dark' | 'light'): Rgb {
  const resolveToken = (varName: string): Rgb => {
    const fromScheme = s.colors[varName]?.[mode]
    if (fromScheme) return toRgb(fromScheme)
    const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
    const scope = mode === 'dark' ? css.slice(0, css.search(/\.light\s*\{/)) : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
    const m = scope.match(new RegExp(`${varName}:\\s*(#[0-9a-fA-F]{6})`))
    if (!m) throw new Error(`the tonal ink references ${varName}, which is neither a scheme token nor a ${mode} value in tokens.css`)
    return toRgb(m[1])
  }
  const schemeToken = resolveToken
  const ref = /^var\(\s*(--[a-z0-9-]+)\s*\)$/.exec(decl)
  if (ref) return schemeToken(ref[1])
  const mixed = /^color-mix\(\s*in srgb\s*,\s*var\(\s*(--[a-z0-9-]+)\s*\)\s+([\d.]+)%\s*,\s*(black|white)\s*\)$/.exec(decl)
  if (mixed) {
    return colorMixSrgb(schemeToken(mixed[1]), Number(mixed[2]) / 100, mixed[3] === 'black' ? [0, 0, 0] : [255, 255, 255])
  }
  const literal = /^(#[0-9a-fA-F]{6})$/.exec(decl)
  if (literal) return toRgb(literal[1])
  throw new Error(`--color-on-primary-tint (${mode}) is "${decl}" — not a var(), a color-mix(in srgb, var() N%, black|white), or a hex`)
}

describe('tonal tint: the ink clears AA over the scheme\'s OWN composited tint, every scheme × mode × ground × state', () => {
  const DECL = { dark: tintInkDecl('dark'), light: tintInkDecl('light') }
  const ALPHAS = tonalAlphas()
  const MODES = ['dark', 'light'] as const

  type Combo = { scheme: string; mode: 'dark' | 'light'; ground: string; state: string; ratio: number }
  const COMBOS: Combo[] = []
  for (const s of SCHEMES) {
    for (const mode of MODES) {
      const ink = resolveTintInk(DECL[mode], s, mode)
      const primary = toRgb(s.colors['--color-primary'][mode])
      for (const [ground, hex] of tonalGrounds(mode)) {
        for (const [state, alpha] of ALPHAS) {
          COMBOS.push({ scheme: s.id, mode, ground, state, ratio: contrastRgb(ink, composite(primary, toRgb(hex), alpha)) })
        }
      }
    }
  }

  it('inspected every scheme across both modes, three grounds and two states', () => {
    expect(SCHEMES.length, 'the curated scheme set').toBeGreaterThanOrEqual(13)
    expect(ALPHAS.map(([s]) => s), 'both tint states, parsed out of the tonal variant').toEqual(['rest', 'hover'])
    expect(ALPHAS.map(([, a]) => a), 'bg-primary/15 at rest, hover:bg-primary/25 on hover').toEqual([0.15, 0.25])
    for (const mode of MODES) {
      const grounds = tonalGrounds(mode)
      expect(grounds.length, `${mode}: three grounds a tonal control sits on`).toBe(3)
      for (const [, hex] of grounds) expect(hex, `${mode} ground is a real hex`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(DECL[mode], `${mode}: the ink declaration was found`).toBeTruthy()
    }
    expect(COMBOS.length, 'every scheme × 2 × 3 × 2 — the whole grid was walked').toBe(SCHEMES.length * 12)
    expect(new Set(COMBOS.map((c) => `${c.scheme}/${c.mode}/${c.ground}/${c.state}`)).size,
      'and every combo is distinct (no scheme silently measured twice)').toBe(SCHEMES.length * 12)
  })

  it('neither mode freezes a hex — the ink is a token reference', () => {
    for (const mode of MODES) {
      expect(DECL[mode], `${mode}: a frozen hex cannot track 12 schemes' tints`)
        .not.toMatch(/^#[0-9a-fA-F]{3,8}$/)
      expect(DECL[mode], `${mode}: must reference a palette token`).toMatch(/^var\(--color-[a-z-]+\)$/)
    }
  })

  it('dark TRACKS the scheme, because dark has no margin to spare', () => {
    expect(DECL.dark).toBe('var(--color-primary-emphasis)')
  })

  it('light may be scheme-INDEPENDENT only while it keeps a wide margin', () => {
    const MARGIN = 6
    const lightWorst = Math.min(...COMBOS.filter((c) => c.mode === 'light').map((c) => c.ratio))
    expect(lightWorst, `a scheme-independent light ink needs headroom, not just AA (got ${lightWorst.toFixed(2)})`)
      .toBeGreaterThanOrEqual(MARGIN)
  })

  it('the no-scheme-applied default matches the default scheme', () => {
    const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
    const defaultScheme = SCHEMES.find((s) => s.id === DEFAULT_SCHEME)!
    const dark = css.slice(0, css.search(/\.light\s*\{/))
    const light = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
    for (const [mode, scope] of [['dark', dark], ['light', light]] as const) {
      for (const varName of ['--color-primary', '--color-primary-emphasis'] as const) {
        const m = scope.match(new RegExp(`${varName}:\\s*(#[0-9a-fA-F]{6})`))
        expect(m?.[1]?.toLowerCase(), `${mode} ${varName} default must match the default scheme`)
          .toBe(defaultScheme.colors[varName][mode].toLowerCase())
      }
    }
  })

  for (const s of SCHEMES) {
    describe(`scheme '${s.id}'`, () => {
      for (const mode of MODES) {
        it(`${mode}: on-primary-tint over every tonal ground, at rest AND on hover, ≥ AA`, () => {
          const rows = COMBOS.filter((c) => c.scheme === s.id && c.mode === mode)
          expect(rows.length, 'three grounds × two states').toBe(6)
          const fails = rows.filter((c) => c.ratio < AA)
            .map((c) => `${c.ground}/${c.state} = ${c.ratio.toFixed(2)}`)
          expect(fails, `tonal label below AA on its own tint:\n  ${fails.join('\n  ')}`).toEqual([])
        })
      }
    })
  }
})
