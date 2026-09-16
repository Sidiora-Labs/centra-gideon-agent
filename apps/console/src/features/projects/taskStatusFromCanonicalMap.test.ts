import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { STATUSES, statusMeta, TERMINAL } from '../tasks/taskMeta'


const SRC = join(import.meta.dirname, "../..")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

describe('the project card reads task status from taskMeta', () => {
  const src = read('features/projects/ProjectsSection.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src, 'the task list moved — this rail measures nothing').toMatch(/onOpenTask\(t\.id\)/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('uses statusMeta rather than its own branch table', () => {
    expect(src, 'the canonical helper must be imported').toMatch(
      /import \{[^}]*\bstatusMeta\b[^}]*\} from '\.\.\/tasks\/taskMeta'/,
    )
    expect(src, 'and called for the row glyph').toMatch(/statusMeta\(t\.status\)/)
  })

  it('names the glyph with the canonical label', () => {
    const at = src.indexOf('statusMeta(t.status)')
    const block = src.slice(at, src.indexOf('</span>', at))
    expect(block.length, 'empty slice — vacuous').toBeGreaterThan(60)
    expect(block, 'role="img" — a graphic whose label is its only text').toMatch(/role="img"/)
    expect(block, "the label must be statusMeta's, not a local string").toMatch(/aria-label=\{sm\.label\}/)
    expect(block, "and the tone must be statusMeta's").toMatch(/color: sm\.tone/)
  })

  it('the strike-through follows TERMINAL, not just done', () => {
    expect(src, 'terminal statuses are struck through').toMatch(/TERMINAL\.has\(t\.status\)/)
    const at = src.indexOf('statusMeta(t.status)')
    const after = src.slice(at, at + 900)
    expect(after, "the row must not re-derive 'finished' from one status").not.toMatch(
      /t\.status === 'done' \? 'text-on-surface-low line-through'/,
    )
  })

  it('no file outside taskMeta keeps a rival status branch table', () => {
    const offenders: string[] = []
    for (const abs of walk(join(SRC, "features"))) {
      if (abs.endsWith('taskMeta.tsx')) continue
      const text = strip(readFileSync(abs, 'utf8'))
      for (const m of text.matchAll(/status === '(?:open|in_progress|blocked|done|cancelled)'[\s\S]{0,900}?/g)) {
        const window = text.slice(m.index!, m.index! + 900)
        const arms = [...window.matchAll(/status === '(?:open|in_progress|blocked|done|cancelled)'\s*\?/g)].length
        const icons = [...window.matchAll(/<(?:Circle\w*|CheckCircle2|XCircle|AlertTriangle)\b/g)].length
        if (arms >= 3 && icons >= 3) {
          offenders.push(`${abs.slice(abs.indexOf('/pages/') + 7)} (${arms} arms, ${icons} icons)`)
          break
        }
      }
    }
    expect(
      offenders,
      'these map status keys to icons themselves. `taskMeta.statusMeta` owns key+label+icon+tone — ' +
        'import it instead of re-deriving it:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('the sweep reads a real tree (vacuity floor)', () => {
    expect(walk(join(SRC, "features")).length, 'the pages sweep found nothing').toBeGreaterThan(60)
  })
})

describe('the canonical map still says what this fix relied on', () => {
  it('in_progress is info-toned, not primary', () => {
    expect(statusMeta('in_progress').tone).toBe('var(--color-info)')
  })

  it('blocked and cancelled each have their own icon', () => {
    const names = new Set(STATUSES.map((s) => s.icon.displayName ?? s.icon.name))
    expect(names.size, 'six statuses must not share icons').toBeGreaterThanOrEqual(4)
    expect(statusMeta('blocked').icon).not.toBe(statusMeta('open').icon)
    expect(statusMeta('cancelled').icon).not.toBe(statusMeta('open').icon)
  })

  it('cancelled resolves to a real entry, so it can never fall through to a default again', () => {
    expect(statusMeta('cancelled').label).not.toBe('Unknown')
    expect(STATUSES.map((s) => s.key)).toContain('cancelled')
  })

  it('TERMINAL is the finished/declined display set', () => {
    expect([...TERMINAL].sort()).toEqual(['cancelled', 'done', 'skipped'])
  })
})
