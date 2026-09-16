
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'

const WEB = process.cwd()
const SRC = join(WEB, 'src')

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
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const rgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
}
const hex = (c: number[]) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

function tintedChipRatio(tone: string, ground: string, pct: number): number {
  const a = pct / 100, I = rgb(tone), G = rgb(ground)
  return contrast(tone, hex(I.map((v, i) => Math.round(a * v + (1 - a) * G[i]))))
}

export function aaFloor(px: number, bold: boolean): 3 | 4.5 {
  const large = px >= 24 || (px >= 18.66 && bold)
  return large ? 3 : 4.5
}

function blocks(): { dark: string; light: string } {
  const src = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8')
  const at = src.search(/\.light\s*\{/)
  if (at < 0) throw new Error('could not find the .light rule block in tokens.css')
  return { dark: src.slice(0, at), light: src.slice(at) }
}
const BLOCKS = blocks()
type Mode = 'dark' | 'light'
function token(name: string, mode: Mode): string {
  const m = BLOCKS[mode].match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!m) throw new Error(`--color-${name} has no direct hex value in the ${mode} block of tokens.css`)
  return m[1]
}


type Site = { file: string; line: number; tone: string; pct: number; literal: boolean }

const TINT =
  /\b(?:background|backgroundColor)\s*:\s*[`'"]?\s*color-mix\(in srgb,\s*(\$\{[^}]+\}|[^,]+?)\s+([\d.]+)%,\s*transparent\)/g

function enclosingObject(src: string, idx: number): string | null {
  let depth = 0, start = -1
  for (let i = idx; i >= 0; i--) {
    const c = src[i]
    if (c === '}') depth++
    else if (c === '{') { if (depth === 0) { start = i; break } depth-- }
  }
  if (start < 0) return null
  depth = 0
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) return src.slice(start, i + 1) }
  }
  return null
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(name) && !/\.(test|doc)\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

function chipSites(): Site[] {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const sites: Site[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(TINT)) {
      const tone = m[1].replace(/^\$\{/, '').replace(/\}$/, '').replace(/^['"`]|['"`]$/g, '').trim()
      const obj = enclosingObject(src, m.index!)
      if (!obj) continue
      const ink = new RegExp(`(?:^|[{,\\s])color:\\s*['"\`]?(?:\\$\\{)?${esc(tone)}(?:\\})?['"\`]?\\s*[,}]`)
      if (!ink.test(obj)) continue
      sites.push({
        file: abs.slice(SRC.length + 1), line: src.slice(0, m.index!).split('\n').length,
        tone, pct: Number(m[2]), literal: /^var\(--color-[a-z-]+\)$/.test(tone),
      })
    }
  }
  return sites
}

const SITES = chipSites()

const RESTING = ['canvas', 'surface', 'surface-low', 'surface-container'] as const

const GLOBAL_TONES = ['ok', 'warn', 'danger'] as const

function inks(mode: Mode): Array<[string, string]> {
  return [
    ...GLOBAL_TONES.map((t) => [t, token(t, mode)] as [string, string]),
    ...SCHEMES.map((s) => [`info:${s.id}`, s.colors['--color-info'][mode]] as [string, string]),
  ]
}

describe('status-chip tone over its own ≤16% tint clears AA on every resting tier, every scheme', () => {
  it('has the full curated scheme set (a sweep over an empty list passes forever)', () => {
    expect(SCHEMES.length, 'curated schemes').toBeGreaterThanOrEqual(12)
    expect(SCHEMES.every((s) => /^#[0-9a-f]{6}$/i.test(s.colors['--color-info'].dark))).toBe(true)
    expect(SCHEMES.every((s) => /^#[0-9a-f]{6}$/i.test(s.colors['--color-info'].light))).toBe(true)
  })

  it('reads real, per-mode ground values out of tokens.css', () => {
    for (const g of RESTING) {
      expect(token(g, 'dark'), `${g} dark`).toMatch(/^#[0-9a-f]{6}$/i)
      expect(token(g, 'dark'), `${g} must differ per mode`).not.toBe(token(g, 'light'))
    }
  })

  it('the three global tones are the same values their aliases resolve to', () => {
    for (const [alias, canon] of [['success', 'ok'], ['warning', 'warn'], ['error', 'danger']]) {
      expect(BLOCKS.dark, `--color-${alias}`).toMatch(
        new RegExp(`--color-${alias}:\\s*var\\(--color-${canon}\\)`),
      )
      expect(BLOCKS.light, `--color-${alias} must not be redeclared in .light`).not.toMatch(
        new RegExp(`--color-${alias}:`),
      )
    }
  })

  for (const mode of ['dark', 'light'] as Mode[]) {
    for (const ground of RESTING) {
      for (const pct of [8, 10, 12, 14, 16]) {
        it(`${mode}: ≥AA on ${ground} at ${pct}%`, () => {
          for (const [name, ink] of inks(mode)) {
            const r = tintedChipRatio(ink, token(ground, mode), pct)
            expect(r, `${name} (${ink}) on its own ${pct}% tint over ${ground} ${mode} = ${r.toFixed(4)}`)
              .toBeGreaterThanOrEqual(aaFloor(12, false))
          }
        })
      }
    }
  }
})

describe('the 18% chip clears AA on every resting tier, every scheme (the retuned token)', () => {
  for (const mode of ['dark', 'light'] as Mode[]) {
    for (const ground of RESTING) {
      it(`${mode}: info ≥AA on ${ground} at 18%`, () => {
        for (const s of SCHEMES) {
          const ink = s.colors['--color-info'][mode]
          const r = tintedChipRatio(ink, token(ground, mode), 18)
          expect(r, `info:${s.id} (${ink}) on its own 18% tint over ${ground} ${mode} = ${r.toFixed(4)}`)
            .toBeGreaterThanOrEqual(aaFloor(13, false))
        }
      })
    }
  }
})

describe('the applicable floor is 4.5, derived from the size, not assumed', () => {
  it('aaFloor implements SC 1.4.3 large text exactly', () => {
    expect(aaFloor(12, false)).toBe(4.5)
    expect(aaFloor(13, false)).toBe(4.5)
    expect(aaFloor(12, true), 'bold does not make 12px large').toBe(4.5)
    expect(aaFloor(18.65, true), 'just under the bold threshold').toBe(4.5)
    expect(aaFloor(18.66, true), '≥18.66px BOLD is large').toBe(3)
    expect(aaFloor(18.66, false), '≥18.66px at normal weight is NOT large').toBe(4.5)
    expect(aaFloor(23.9, false)).toBe(4.5)
    expect(aaFloor(24, false), '≥24px at any weight is large').toBe(3)
  })

  it('every type size declared on a chip in this family is under the large-text threshold', () => {
    const sizes: Array<{ site: string; px: number }> = []
    for (const s of SITES) {
      const src = readFileSync(join(SRC, s.file), 'utf8').split('\n')
      const window = [src[s.line - 2] ?? '', src[s.line - 1] ?? ''].join(' ')
      for (const m of window.matchAll(/text-\[([\d.]+)(rem|px)\]/g)) {
        sizes.push({ site: `${s.file}:${s.line}`, px: Number(m[1]) * (m[2] === 'rem' ? 16 : 1) })
      }
    }
    expect(sizes.length, 'chip sites that declare an explicit type size').toBeGreaterThanOrEqual(15)
    const large = sizes.filter((s) => s.px >= 18.66)
    expect(large, `these could claim large-text relief and the sweep above assumes they cannot:\n${
      large.map((s) => `${s.site} — ${s.px}px`).join('\n')}`).toEqual([])
  })
})

describe('the tone-as-ink-and-tint family stays inside the swept envelope', () => {
  it('the census is not vacuously empty', () => {
    expect(SITES.length, 'tone-as-ink-and-background-tint sites').toBeGreaterThanOrEqual(80)
    expect(SITES.filter((s) => s.literal).length, 'literal-tone sites').toBeGreaterThanOrEqual(60)
    expect(SITES.filter((s) => !s.literal).length, 'registry-tone sites').toBeGreaterThanOrEqual(20)
    expect(SITES.some((s) => s.file === 'features/tasks/TasksListPage.tsx' && s.pct === 16)).toBe(true)
    expect(SITES.some((s) => s.file === 'features/tasks/TaskDetail.tsx' && s.pct === 18)).toBe(true)
  })

  const key = (s: Site) => `${s.file} @${s.pct}% ${s.tone.replace(/\s+/g, ' ').trim()}`

  const ABOVE_CEILING = new Set([
    'features/knowledge/KnowledgeListPage.tsx @20% tone',
    "features/ChatPage.tsx @22% t.color || 'var(--color-primary)",
  ])

  it('no chip tints above the 18% ceiling these tiers were swept at', () => {
    const over = SITES.filter((s) => s.pct > 18).map(key)
    const unexpected = over.filter((s) => !ABOVE_CEILING.has(s))
    expect(unexpected, `above the swept ceiling with no recorded reason:\n${unexpected.join('\n')}\n` +
      `Sweep that strength in this file first, or use one that is already swept (≤16% on any resting ` +
      `tier, 18% on --color-surface).`).toEqual([])
    expect(over.length, 'the two recorded exceptions still exist (or this allowance is stale)').toBe(ABOVE_CEILING.size)
  })

  it('the 18% population is exactly the eight sites whose tones were reasoned about', () => {
    const at18 = SITES.filter((s) => s.pct === 18)
    expect(at18.map(key).sort(), 'a new 18% chip needs its resting tier measured before it can be added here')
      .toEqual([
        'features/ChatPage.tsx @18% var(--color-secondary)',
        "features/ChatPage.tsx @18% tagById[tid].color || 'var(--color-primary)",
        'features/artifacts/ArtifactViewer.tsx @18% var(--color-warning)',
        "features/loops/LoopCockpitPage.tsx @18% verdict.done ? 'var(--color-ok)' : 'var(--color-primary)",
        'features/prompts/VariableRow.tsx @18% var(--color-danger)',

        "features/skills/MarketplaceDetail.tsx @18% var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})",
        'features/tasks/TaskDetail.tsx @18% sm.tone',
        'shared/ui/UpdateProgressOverlay.tsx @18% var(--color-success)',
      ].sort())
    expect(at18.length, '18% chip sites').toBe(8)
  })
})

describe('--color-info is declared once, in three places that must agree', () => {
  const coral = SCHEMES.find((s) => s.id === 'coral')!
  it('tokens.css matches the coral scheme', () => {
    expect(token('info', 'dark')).toBe(coral.colors['--color-info'].dark)
    expect(token('info', 'light')).toBe(coral.colors['--color-info'].light)
  })
  it('tokenRegistry matches the coral scheme', () => {
    const reg = readFileSync(join(SRC, 'shared/theme/tokenRegistry.ts'), 'utf8')
    const m = reg.match(/c\('--color-info',[^)]*'(#[0-9a-fA-F]{6})',\s*'(#[0-9a-fA-F]{6})'\)/)
    expect(m, "tokenRegistry declares --color-info with two hex values").toBeTruthy()
    expect(m![1]).toBe(coral.colors['--color-info'].dark)
    expect(m![2]).toBe(coral.colors['--color-info'].light)
  })
})
