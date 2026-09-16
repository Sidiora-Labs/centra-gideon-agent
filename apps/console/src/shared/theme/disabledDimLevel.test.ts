import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const ESTABLISHED = new Set(['40', '50', '60'])
const PINNED_EXCEPTIONS = new Map([['features/knowledge/KnowledgeListPage.tsx', '70']])

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const codeOf = (s: string): string => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/(^|[^:])\/\/[^\n]*/g, (m, p) => p + ' '.repeat(m.length - p.length))

function levels(): Array<{ file: string; line: number; level: string }> {
  const out: Array<{ file: string; line: number; level: string }> = []
  for (const abs of walk(SRC)) {
    const text = codeOf(readFileSync(abs, 'utf8'))
    for (const m of text.matchAll(/(?<!aria-)disabled:opacity-(\d+)/g)) {
      out.push({
        file: abs.slice(SRC.length + 1),
        line: text.slice(0, m.index).split('\n').length,
        level: m[1],
      })
    }
  }
  return out
}

describe('disabled dim level', () => {
  const all = levels()

  it('finds the disabled-opacity sites (not vacuously green)', () => {
    expect(all.length, 'the matcher must find disabled:opacity-* sites').toBeGreaterThan(50)
  })

  it('uses only established levels, or a pinned exception', () => {
    const stray = all.filter(
      (s) => !ESTABLISHED.has(s.level) && PINNED_EXCEPTIONS.get(s.file) !== s.level,
    )
    expect(
      stray.map((s) => `${s.file}:${s.line} → opacity-${s.level}`),
      'a disabled control dims at a level nothing else in the tree uses. Match the level used by ' +
        'comparable elements: 40 for controls (Button/Toggle/Slider), 50 for text and text-like ' +
        '(forms/TextLink), 60 for prose (Markdown). If the site genuinely needs its own level, ' +
        'pin it in PINNED_EXCEPTIONS with the reason:\n  ' +
        stray.map((s) => `${s.file}:${s.line} → opacity-${s.level}`).join('\n  '),
    ).toEqual([])
  })

  it('keeps the pinned exception honest — it must still exist', () => {
    for (const [file, level] of PINNED_EXCEPTIONS) {
      const hit = all.some((s) => s.file === file && s.level === level)
      expect(hit, `PINNED_EXCEPTIONS lists ${file} → opacity-${level}, which no longer exists. ` +
        'Delete the entry (and its comment) rather than leaving a stale exemption.').toBe(true)
    }
  })

  it('controls converged: no site dims a control at 30 or 45', () => {
    const bad = all.filter((s) => s.level === '30' || s.level === '45')
    expect(bad.map((s) => `${s.file}:${s.line}`), 'converged to 40 in this change').toEqual([])
  })
})
