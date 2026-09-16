import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'



const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const FIXED: Array<{ file: string; reason: string; guard: RegExp }> = [
  { file: 'features/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!dirty \|\| undefined\}/ },
  { file: 'features/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!botDirty \|\| undefined\}/ },
  { file: 'shared/ui/content/ContentSurface.tsx', reason: 'no changes to save', guard: /aria-disabled=\{\(!dirty && !saving\) \|\| undefined\}/ },
  { file: 'features/tasks/formControls.tsx', reason: 'That would create a dependency cycle', guard: /aria-disabled=\{cyclic \|\| undefined\}/ },
  { file: 'features/knowledge/KnowledgeDetail.tsx', reason: 'Nothing more to show', guard: /aria-disabled=\{!hasMore \|\| undefined\}/ },
]

describe('a converted raw control keeps its tab stop AND its dimming', () => {
  for (const { file, reason, guard } of FIXED) {
    it(`${file} — "${reason}"`, () => {
      const src = readFileSync(join(SRC, file), 'utf8')
      expect(src, 'the gate must publish aria-disabled, not the native attribute').toMatch(guard)
      expect(src.toLowerCase(), 'and it must say why').toContain(reason.toLowerCase())
      const softTags = [...src.matchAll(/<button\b[\s\S]{0,900}?>/g)]
        .map((m) => m[0])
        .filter((t) => /aria-disabled=/.test(t) && /disabled:opacity-40/.test(t))
      for (const t of softTags) {
        expect(t, 'a soft-off tag needs aria-disabled:opacity-40 as well').toMatch(/aria-disabled:opacity-40/)
      }
    })
  }

  it('neutralises the hover tint that `enabled:` starts allowing', () => {
    const src = readFileSync(join(SRC, 'features/tasks/formControls.tsx'), 'utf8')
    expect(src).toMatch(/enabled:hover:bg-surface-high aria-disabled:hover:bg-transparent/)
  })

  it('refuses the click it can no longer refuse natively', () => {
    for (const rel of ['features/settings/AccountPanel.tsx', 'features/tasks/formControls.tsx', 'features/knowledge/KnowledgeDetail.tsx', 'shared/ui/content/ContentSurface.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must guard its handler`).toMatch(/onClick=\{[^}\n]*\?[^\n]*undefined/)
    }
  })
})

describe('the remaining raw disabled buttons are accounted for', () => {
  const BUSY = /\b(busy|saving|sending|loading|installing|pending|working|submitting|launching|testing|running|deleting|creating|refreshing|syncing|starting|stopping|genning|importing|exporting|uploading|repairing|regen\w*|retrying|reloading|applying|generating|fetching|polling|checking)\b/i

  const ACCOUNTED = new Map<string, string>([
    ['shared/ui/HeaderActions.tsx', 'pass-through `disabled` on a primitive — the caller owns the reason'],
    ['shared/ui/ProjectPicker.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/Segmented.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/TextLink.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/Toggle.tsx', 'pass-through `disabled` on a primitive'],
    ['features/settings/ProjectionRulesPanel.tsx', 'pass-through `disabled` from its caller'],
    ['features/settings/SecurityPanel.tsx', 'pass-through `disabled` from its caller'],
    ['features/loops/LoopPlanReview.tsx', 'self-evident — the label reads "Installed" when done'],
    ['features/tasks/TaskDetail.tsx', 'NOT AN ACTION: read-only + capability gates, awaiting the #1168 shape'],
  ])

  const unaccounted = walk(SRC).flatMap((f) => {
    const rel = f.slice(SRC.length + 1)
    if (ACCOUNTED.has(rel)) return []
    const src = readFileSync(f, 'utf8')
    return [...src.matchAll(/<button\b[^>]{0,600}?(?<!aria-)disabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/gs)]
      .filter((m) => m[1].split(/\|\||&&/).map((s) => s.trim()).filter(Boolean).some((c) => !BUSY.test(c)))
      .map(() => rel)
  })

  it('leaves none unclassified', () => {
    expect([...new Set(unaccounted)], 'a raw disabled button with a gate a user could act on').toEqual([])
  })

  it('still finds the population it is filtering (not vacuously green)', () => {
    const all = walk(SRC).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/<button\b[^>]{0,600}?(?<!aria-)disabled=\{/gs)].map(() => 1))
    expect(all.length, 'the matcher must find the raw disabled buttons').toBeGreaterThanOrEqual(30)
  })
})
