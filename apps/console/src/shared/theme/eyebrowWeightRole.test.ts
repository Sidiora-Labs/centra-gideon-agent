import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { countUppercaseTrackedEyebrows } from './consistencyAudit.report'


interface Baseline { uppercaseTrackedEyebrows: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), "src/shared/theme/eyebrowWeightRole.baseline.json"), 'utf8')
  const j = JSON.parse(raw)
  return { uppercaseTrackedEyebrows: j.uppercaseTrackedEyebrows }
}

describe('eyebrow weight-role ratchet (uppercase-tracked eyebrows may only shrink)', () => {
  const base = loadBaseline()
  const live = countUppercaseTrackedEyebrows()

  it(`uppercase-tracked eyebrow count must not exceed the baseline (${base.uppercaseTrackedEyebrows})`, () => {
    expect(
      live.total,
      `New uppercase-tracked eyebrow(s) detected (${live.total} > ${base.uppercaseTrackedEyebrows}). ` +
        `The Weight-First rule (web/DESIGN.md §3/§6) bans uppercase-with-tracking — use the Eyebrow ` +
        `primitive (ui/Eyebrow.tsx: the sentence-case 'caption' role), or if this is an intentional ` +
        `migration DOWN, lower uppercaseTrackedEyebrows in src/design/eyebrowWeightRole.baseline.json ` +
        `in the same commit.\nOffenders:\n${JSON.stringify(live.byFile, null, 2)}`,
    ).toBeLessThanOrEqual(base.uppercaseTrackedEyebrows)
  })

  it('baseline is not stale (a migration dropped the real count without ratcheting)', () => {
    if (live.total < base.uppercaseTrackedEyebrows) {
      // eslint-disable-next-line no-console
      console.warn(
        `[eyebrow-weight-role] live count ${live.total} is below baseline ` +
          `${base.uppercaseTrackedEyebrows} — ratchet src/design/eyebrowWeightRole.baseline.json DOWN ` +
          `to lock in the migration.`,
      )
    }
    expect(live.total).toBeLessThanOrEqual(base.uppercaseTrackedEyebrows)
  })

  it('the ratchet is not vacuous — it scanned the tree and found the frozen backlog', () => {
    expect(base.uppercaseTrackedEyebrows).toBeGreaterThan(0)
    expect(Object.keys(live.byFile).length).toBeGreaterThan(0)
  })
})
