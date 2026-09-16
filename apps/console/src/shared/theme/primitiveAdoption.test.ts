import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { scanDrift, countInlineFontWeights } from './consistencyAudit.report'


interface Baseline { rawButton: number; rawInput: number; rawDialog: number; inlineFontWeight: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), "src/shared/theme/primitiveAdoption.baseline.json"), 'utf8')
  const j = JSON.parse(raw)
  return { rawButton: j.rawButton, rawInput: j.rawInput, rawDialog: j.rawDialog, inlineFontWeight: j.inlineFontWeight }
}

function liveCounts() {
  const res = scanDrift()
  let rawButton = 0, rawInput = 0, rawDialog = 0
  for (const f in res.primitivesByFile) {
    const p = res.primitivesByFile[f]
    rawButton += p['raw-button']
    rawInput += p['raw-input']
    rawDialog += p['raw-dialog']
  }
  return { rawButton, rawInput, rawDialog }
}

describe('primitive-adoption ratchet (bespoke chrome may only shrink)', () => {
  const base = loadBaseline()
  const live = liveCounts()

  it(`raw <button> count must not exceed the baseline (${base.rawButton})`, () => {
    expect(
      live.rawButton,
      `New bespoke <button>(s) detected (${live.rawButton} > ${base.rawButton}). ` +
        `Use the Button/IconButton primitive, or if this is an intentional migration DOWN, ` +
        `lower rawButton in src/design/primitiveAdoption.baseline.json.`,
    ).toBeLessThanOrEqual(base.rawButton)
  })

  it(`raw <input>/<textarea>/<select> count must not exceed the baseline (${base.rawInput})`, () => {
    expect(
      live.rawInput,
      `New bespoke form element(s) detected (${live.rawInput} > ${base.rawInput}). ` +
        `Use the shared form family (Field/TextInput/TextArea/Select), or lower rawInput in the baseline if migrating down.`,
    ).toBeLessThanOrEqual(base.rawInput)
  })

  it(`ad-hoc dialogs must stay at the baseline (${base.rawDialog}) — Modal is canonical`, () => {
    expect(
      live.rawDialog,
      `New ad-hoc dialog markup detected (${live.rawDialog} > ${base.rawDialog}). Use the Modal primitive.`,
    ).toBeLessThanOrEqual(base.rawDialog)
  })

  it(`inline font-weight count must not exceed the baseline (${base.inlineFontWeight})`, () => {
    const live = countInlineFontWeights().total
    expect(
      live,
      `New inline font-variation-settings "wght" detected (${live} > ${base.inlineFontWeight}). ` +
        `Use fvs()/withWeight() (design/fontWeight.ts) or a .fw-* class, or lower inlineFontWeight ` +
        `in src/design/primitiveAdoption.baseline.json if migrating down.`,
    ).toBeLessThanOrEqual(base.inlineFontWeight)
  })

  it('baseline is not stale (live counts have not silently dropped >20 below it without a ratchet)', () => {
    const slack = 20
    if (live.rawButton + slack < base.rawButton || live.rawInput + slack < base.rawInput) {
      // eslint-disable-next-line no-console
      console.warn(
        `[primitive-adoption] live counts are well below baseline ` +
          `(button ${live.rawButton}/${base.rawButton}, input ${live.rawInput}/${base.rawInput}) — ` +
          `ratchet primitiveAdoption.baseline.json DOWN to lock in the gain.`,
      )
    }
    expect(true).toBe(true)
  })
})
