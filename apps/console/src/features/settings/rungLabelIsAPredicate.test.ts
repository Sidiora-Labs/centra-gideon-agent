import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/settings/GuardrailsPanel.tsx")
const raw = readFileSync(SRC, 'utf8')

describe('a rung label is never dropped into a noun slot', () => {
  it('the promote button gives the label a subject', () => {
    expect(raw, 'the button must read as a sentence').toContain(
      'Promote so it {rungMeta(t.next_rung, ladder).label}',
    )
    expect(raw, '"Promote to <predicate>" is the defect').not.toContain('Promote to {rungMeta')
  })

  it('the success toasts — the canonical form — are untouched', () => {
    expect(raw).toContain('now ${rungMeta(r.rung, ladder ?? null).label}.')
    expect(raw).toContain('is back at ${rungMeta(t.floor, ladder ?? null).label}.')
  })

  it('no label interpolation directly follows "to " or "at " in a NEW slot', () => {
    const ALLOWED_PREFIXES = [
      'now ',
      'so it ',
      'is back at ',
      'back to ',
    ]
    const offenders: string[] = []
    for (const m of raw.matchAll(/rungMeta\([^)]*\)\.label/g)) {
      const before = raw.slice(Math.max(0, m.index! - 60), m.index!).replace(/\$?\{$/, '')
      if (!ALLOWED_PREFIXES.some((p) => before.endsWith(p))) {
        offenders.push(`…${before.slice(-30)}» ${m[0]}`)
      }
    }
    expect(offenders, 'a rung label needs a subject, or one of the two allowed idioms').toEqual([])
  })

  it('the ratchet is not vacuous — it finds every label slot in the file', () => {
    expect([...raw.matchAll(/rungMeta\([^)]*\)\.label/g)].length).toBeGreaterThanOrEqual(4)
  })
})
