import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { accentChip } from './accent'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

describe('accentChip', () => {
  it('uses the container pair, not the accent as ink', () => {
    expect(accentChip.background).toBe('var(--color-primary-container)')
    expect(accentChip.color).toBe('var(--color-on-primary-container)')
  })

  it('carries no tint strength to drift', () => {
    expect(JSON.stringify(accentChip)).not.toMatch(/color-mix|%/)
  })
})

describe('no primary tint under primary ink survives', () => {
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    text.split('\n').forEach((line, i) => {
      const bg = /background:\s*'?`?color-mix\(in srgb, var\(--color-primary\) \d+%/.test(line)
      const ink = /color:\s*'?var\(--color-primary\)/.test(line)
      if (bg && ink) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
    })
  }

  it('has none left', () => {
    expect(
      offenders,
      `the accent is still both tint and ink (3.33–3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})


const CLASS_TINT_ALLOWED = new Set([
  'shared/ui/Button.tsx',
])

describe('no primary tint under primary ink survives — utility spelling', () => {
  const offenders: string[] = []
  const seen: string[] = []
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
      if (!/bg-primary\/\d+/.test(line)) return
      if (!/text-primary\b/.test(line)) return
      seen.push(`${rel}:${i + 1}`)
      if (!CLASS_TINT_ALLOWED.has(rel)) offenders.push(`${rel}:${i + 1}`)
    })
  }

  it('has none left outside the two recorded interactive holdouts', () => {
    expect(
      offenders,
      `coral ink on a coral tint is 3.64–4.20:1 in light — use bg-primary-container + text-on-primary-container:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('still finds the holdouts — the allowlist is not stale', () => {
    expect(seen.length, 'the matcher stopped matching anything at all').toBeGreaterThan(0)
    for (const rel of CLASS_TINT_ALLOWED) {
      expect(seen.some((s) => s.startsWith(rel + ':')), `${rel} no longer carries the pattern — drop it from the allowlist`).toBe(true)
    }
  })
})

describe('the sweep actually adopted the shared definition', () => {
  const adopters = walk(SRC).filter((abs) => /\baccentChip\b/.test(readFileSync(abs, 'utf8')))

  it('is used across the tree, not in one corner', () => {
    expect(adopters.length, 'adopters of the shared accent chip').toBeGreaterThanOrEqual(20)
  })

  it('every adopter imports it rather than re-declaring the colours', () => {
    const bad = adopters
      .filter((abs) => !abs.endsWith(join('shared/theme', 'accent.ts')))
      .filter((abs) => !/import \{[^}]*accentChip[^}]*\} from '[^']*design\/accent'/.test(readFileSync(abs, 'utf8')))
    expect(bad.map((b) => b.slice(SRC.length + 1)), 'uses accentChip without importing it').toEqual([])
  })
})


describe('the third spelling has a home', () => {
  it('is swept behaviourally next door, not silently ignored here', () => {
    expect(readFileSync(join(SRC, 'shared/theme/accentChipTone.test.tsx'), 'utf8'))
      .toMatch(/a rung chip inks coral through the container pair/)
  })
})

describe('no primary tint under CLASS-spelled primary ink survives', () => {
  const TINT = /color-mix\(in srgb, var\(--color-primary\) \d+%/g
  const INK_CLASS = /className="[^"]*\btext-primary\b[^"]*"/
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(TINT)) {
      const window = text.slice(Math.max(0, m.index! - 320), m.index!)
      if (INK_CLASS.test(window)) {
        offenders.push(`${abs.slice(SRC.length + 1)}:${text.slice(0, m.index!).split('\n').length}`)
      }
    }
  }

  it('has none left', () => {
    expect(
      offenders,
      `class-spelled accent ink over a primary tint (3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the matcher still recognises the shape it polices — not vacuously green', () => {
    const sample = [
      '        <span className="shrink-0 rounded px-1.5 text-[0.75rem] text-primary"',
      "          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>",
    ].join('\n')
    const hit = [...sample.matchAll(TINT)].some((m) => INK_CLASS.test(sample.slice(0, m.index!)))
    expect(hit, 'the detector matches the spelling that shipped three times').toBe(true)
    const benign = sample.replace(' text-primary', ' text-on-surface-low')
    const falsePositive = [...benign.matchAll(TINT)].some((m) => INK_CLASS.test(benign.slice(0, m.index!)))
    expect(falsePositive, 'a primary tint under non-accent ink is left alone').toBe(false)
  })
})
