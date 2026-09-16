import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'


const SRC = join(process.cwd(), "src")
const BASELINE = join(SRC, "shared/theme", 'windowedListAdoption.baseline.json')

interface Baseline { adopters: number; surfaces: string[]; before: { samples: unknown[] }; after: unknown }
const base: Baseline = JSON.parse(readFileSync(BASELINE, 'utf8'))

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx$/.test(e) && !/\.test\.tsx$/.test(e)) out.push(p)
  }
  return out
}

const adopters = walk(SRC)
  .filter((p) => /<WindowedList[\s>]/.test(readFileSync(p, 'utf8')))
  .map((p) => relative(SRC, p).split(/[\\/]/).join('/'))
  .sort()

describe('windowing adoption ratchet (the window may only spread, never retreat)', () => {
  it(`at least ${base.adopters} surfaces render <WindowedList>`, () => {
    expect(
      adopters.length,
      `A surface dropped ui/WindowedList (${adopters.length} < ${base.adopters}). Long lists degrade `
        + `as they grow — that is measured, not asserted (see windowedListAdoption.baseline.json). `
        + `If a surface genuinely no longer needs it, lower "adopters" AND remove it from `
        + `"surfaces" in the baseline, in the same commit, with the reason in the plan's Execution log. `
        + `Live: ${adopters.join(', ')}`,
    ).toBeGreaterThanOrEqual(base.adopters)
  })

  it('every surface the baseline names still adopts it, by name', () => {
    const missing = base.surfaces.filter((s) => !adopters.includes(s))
    expect(
      missing,
      `These surfaces are recorded as windowed but no longer render <WindowedList>: ${missing.join(', ')}. `
        + `An adoption count that stays flat while a NAMED long-list surface drops out is the `
        + `"added once and left unused" failure DSC-13 exists to prevent.`,
    ).toEqual([])
  })

  it('the five surfaces the atom names are the five that adopted it', () => {
    for (const [surface, file] of [
      ['knowledge items', 'features/knowledge/KnowledgeListPage.tsx'],
      ['sessions', 'features/ChatPage.tsx'],
      ['runs', 'features/workflows/WorkflowsListPage.tsx'],
      ['inbox', 'features/inbox/InboxPage.tsx'],
      ['logs', 'features/settings/DiagnosticsPanel.tsx'],
    ] as const) {
      expect(adopters, `${surface} (${file}) must window`).toContain(file)
    }
  })

  it('every adopter DECLARES its row-height constraint rather than assuming one', () => {
    const undeclared: string[] = []
    for (const rel of adopters) {
      const text = readFileSync(join(SRC, rel), 'utf8')
      const opens = (text.match(/<WindowedList[\s>]/g) ?? []).length
      const declared = (text.match(/rowHeights=["{]/g) ?? []).length
      if (declared < opens) undeclared.push(`${rel} (${opens} lists, ${declared} declarations)`)
    }
    expect(undeclared, `rowHeights must be stated per list: ${undeclared.join('; ')}`).toEqual([])
  })

  it('every adopter states a find-in-page alternative', () => {
    const bad: string[] = []
    for (const rel of adopters) {
      const text = readFileSync(join(SRC, rel), 'utf8')
      const opens = (text.match(/<WindowedList[\s>]/g) ?? []).length
      const hints = [...text.matchAll(/findHint=(?:"([^"]*)"|\{["'`]([^"'`]*)["'`]\})/g)]
        .map((m) => m[1] ?? m[2] ?? '')
        .filter((h) => h.trim().length > 20)
      if (hints.length < opens) bad.push(`${rel} (${opens} lists, ${hints.length} substantive hints)`)
    }
    expect(bad, `findHint must name a real affordance: ${bad.join('; ')}`).toEqual([])
  })

  it('the recorded measurement is a real before/after, not a placeholder', () => {
    type S = { rows: number; domRows: number; keystrokeMs: number; frameMedianMs: number; mountMs: number }
    const before = base.before.samples as S[]
    const after = (base.after as { samples?: S[] }).samples
    expect(before.length).toBeGreaterThanOrEqual(4)
    expect(after, 'the "after" half of the measurement must be measured, not promised').toBeDefined()
    expect(after!.length).toBe(before.length)

    const domRows = after!.map((s) => s.domRows)
    expect(Math.max(...domRows) - Math.min(...domRows)).toBeLessThanOrEqual(4)
    const beforeRows = before.map((s) => s.domRows)
    expect(Math.max(...beforeRows)).toBe(Math.max(...before.map((s) => s.rows)))

    const worstN = Math.max(...before.map((s) => s.rows))
    const b = before.find((s) => s.rows === worstN)!
    const a = after!.find((s) => s.rows === worstN)!
    expect(a.frameMedianMs).toBeLessThan(b.frameMedianMs / 4)
    expect(a.keystrokeMs).toBeLessThan(b.keystrokeMs / 4)
    expect(a.mountMs).toBeLessThan(b.mountMs / 4)
  })

  it('the ratchet is not vacuous — it really found the primitive in the tree', () => {
    expect(adopters.length).toBeGreaterThan(0)
    expect(readFileSync(join(SRC, "shared/ui", 'WindowedList.tsx'), 'utf8')).toContain('WINDOWING_THRESHOLD')
  })
})
