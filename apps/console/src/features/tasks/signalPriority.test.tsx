import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { signalPriority, priorityMeta } from './taskMeta'


describe('signalPriority', () => {
  it('is silent for the default', () => {
    expect(signalPriority('medium')).toBeNull()
  })

  it('is silent when unset — the backend cannot tell that from medium', () => {
    expect(signalPriority(undefined)).toBeNull()
    expect(signalPriority('')).toBeNull()
  })

  it.each(['critical', 'high', 'low', 'trivial'])('still shows %s — an explicit choice', (k) => {
    const pm = signalPriority(k)
    expect(pm, `${k} is a deliberate choice and must stay visible`).not.toBeNull()
    expect(pm!.key).toBe(k)
  })

  it('shows an unrecognised rung verbatim (the backend keeps any string)', () => {
    const pm = signalPriority('p0')
    expect(pm).not.toBeNull()
    expect(pm!.label).toBe('p0')
  })

  it('agrees with priorityMeta wherever it is not silent', () => {
    for (const k of ['critical', 'high', 'low', 'trivial', 'p0']) {
      expect(signalPriority(k)).toEqual(priorityMeta(k))
    }
  })
})


const read = (rel: string) => readFileSync(join(process.cwd(), "src/features/tasks", rel), 'utf8')

const SRC_ROOT = join(import.meta.dirname, "../..")
const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const RIVAL_TONE_MAP = /critical:\s*'var\(--color-[^)]+\)'[\s\S]{0,160}?high:\s*'var\(--color-/

const walkSrc = (d: string = SRC_ROOT): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walkSrc(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

describe('every BROWSING view is signal-only; the detail view is not', () => {
  it.each([
    ['features/tasks/TasksListPage.tsx', 2],
    ['features/tasks/TaskBoard.tsx', 1],
    ['features/tasks/TaskGraph.tsx', 1],
    ['features/dashboard/widgets/TasksWidget.tsx', 1],
    ['features/companion/CompanionSections.tsx', 1],
  ])('%s calls signalPriority (%i site(s))', (rel, n) => {
    const src = readFileSync(join(SRC_ROOT, rel), 'utf8')
    expect([...src.matchAll(/signalPriority\(/g)].length, `${rel} must use the signal-only helper`).toBeGreaterThanOrEqual(n)
    expect(src, `${rel} must not fall back to the always-render helper`).not.toMatch(/=\s*priorityMeta\(/)
  })

  it('NOWHERE in the tree keeps a rival priority→tone map', () => {
    const offenders: string[] = []
    for (const abs of walkSrc()) {
      if (abs.endsWith('taskMeta.tsx')) continue
      const src = stripComments(readFileSync(abs, 'utf8'))
      if (RIVAL_TONE_MAP.test(src)) {
        offenders.push(abs.slice(abs.indexOf('/src/') + 5))
      }
    }
    expect(
      offenders,
      'these files map priority rungs to colours themselves. `taskMeta` owns that map — import ' +
        '`signalPriority` (browsing) or `priorityMeta` (an editor) instead:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('and the tree-wide scan actually reads the tree (vacuity floor)', () => {
    const files = walkSrc()
    expect(files.length, 'the .tsx sweep found nothing — the scan root is wrong').toBeGreaterThan(200)
  })

  it('the rival-map detector fires on the shape it was written from', () => {
    const rival = `const PRIORITY_TONE: Record<string, string> = {
      critical: 'var(--color-danger)', high: 'var(--color-warn)',
      medium: 'var(--color-info)', low: 'var(--color-on-surface-low)',
    }`
    expect(RIVAL_TONE_MAP.test(rival), 'the detector no longer catches a rival Record map').toBe(true)

    const canonicalShape = `export const PRIORITIES: PriorityMeta[] = [
      { key: 'critical', label: 'Critical', tone: 'var(--color-danger)' },
      { key: 'high', label: 'High', tone: 'var(--color-warn)' },
    ]`
    expect(
      RIVAL_TONE_MAP.test(canonicalShape),
      'the detector matches the canonical keyed-array form, so it would red taskMeta itself',
    ).toBe(false)
  })

  it('TaskDetail deliberately keeps priorityMeta — a field value belongs in its editor', () => {
    const src = read('TaskDetail.tsx')
    expect(src, 'the detail panel must keep showing the current value').toMatch(/priorityMeta\(task\.priority\)/)
    expect(src, 'and must not adopt the browsing-surface helper').not.toMatch(/signalPriority/)
  })
})

describe('the meta line cannot strand its separator', () => {
  const src = read('TasksListPage.tsx')

  it('carries no leading dot at all — the conditional one still stranded', () => {
    const code = src
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    expect(code, 'no glyph, so neither an empty lead nor a wrap can strand one')
      .not.toMatch(/'· '/)
    expect(code, 'and the presence guard that only half-solved it is gone')
      .not.toMatch(/lead\.length > 0 \|\| i > 0/)
  })

  it('renders nothing at all when the whole line would be empty', () => {
    expect(src).toMatch(/if \(lead\.length === 0 && tail\.length === 0 && !comments\) return null/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function MetaLine\(/)
    expect(src.length).toBeGreaterThan(2000)
  })
})
