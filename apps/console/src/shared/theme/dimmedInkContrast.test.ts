import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function classAttributes(): Array<{ file: string; line: number; value: string }> {
  const out: Array<{ file: string; line: number; value: string }> = []
  for (const abs of walk(SRC)) {
    readFileSync(abs, 'utf8').split('\n').forEach((ln, i) => {
      for (const m of ln.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        out.push({ file: abs.slice(SRC.length + 1), line: i + 1, value: m[1] ?? m[2] ?? '' })
      }
    })
  }
  return out
}

const MEASURED = new Set([
  'features/projects/ProjectsSection.tsx',
  'features/tasks/TaskGraph.tsx',
])
const DIMMED_LOW_INK = /\btext-on-surface-low\/\d+\b/

describe('the measured surfaces keep their ink undimmed', () => {
  it.each([...MEASURED])('%s has no dimmed low-ink TEXT', (file) => {
    const hits = classAttributes()
      .filter((c) => c.file === file && DIMMED_LOW_INK.test(c.value))
      .filter((c) => !/\bsize-\d|<Circle|mt-0\.5 shrink-0/.test(c.value))
      .map((c) => `${c.file}:${c.line}  ${c.value.replace(/\s+/g, ' ').slice(0, 80)}`)
    expect(
      hits,
      `text-on-surface-low is already the faintest token (6.67:1); an opacity suffix takes it to ` +
        `3.86:1, below AA 1.4.3:\n  ${hits.join('\n  ')}`,
    ).toEqual([])
  })

  it('the placeholder still reads as a placeholder', () => {
    const src = readFileSync(join(SRC, 'features/projects/ProjectsSection.tsx'), 'utf8')
    expect(src).toMatch(/text-on-surface-low text-\[0\.8125rem\] italic">No workspace bound/)
  })
})

describe('the rail is not vacuously green', () => {
  it('it scans real className strings', () => {
    const all = classAttributes()
    expect(all.length, 'the extractor must find the tree\'s className strings').toBeGreaterThan(2000)
    for (const f of MEASURED) {
      expect(all.some((c) => c.file === f), `${f} must be in scope`).toBe(true)
    }
  })

  it('it still FLAGS the shape it guards', () => {
    expect(DIMMED_LOW_INK.test('flex-1 text-on-surface-low/70 text-[0.8125rem] italic')).toBe(true)
    expect(DIMMED_LOW_INK.test('flex-1 text-on-surface-low text-[0.8125rem] italic')).toBe(false)
    expect(DIMMED_LOW_INK.test('border-outline-variant/40 bg-surface-container/60')).toBe(false)
  })
})
