import { describe, expect, it } from 'vitest'
import { cascadeConfirmation, revalidateNotice, revalidateSummary } from './revalidate'


describe('revalidateNotice', () => {
  it('names re-validation as the thing to do after resuming', () => {
    expect(revalidateNotice).toMatch(/re-validate/i)
    expect(revalidateNotice).toMatch(/calibrat/i)
  })
})

describe('revalidateSummary', () => {
  it('names the re-run count so the user sees how much the edit invalidated', () => {
    expect(revalidateSummary({ rerun: ['a', 'b', 'c'], stale: [], skipped: [], committed_effects: [], needs_confirmation: false }))
      .toBe('Edit applied — 3 steps will re-run. Re-validate this template’s judge calibration.')
  })

  it('singularizes a one-step cascade', () => {
    expect(revalidateSummary({ rerun: ['a'], stale: [], skipped: [], committed_effects: [], needs_confirmation: false }))
      .toMatch(/1 step will re-run/)
  })

  it('still asks to re-validate when nothing re-runs', () => {
    const s = revalidateSummary({ rerun: [], stale: [], skipped: [], committed_effects: [], needs_confirmation: false })
    expect(s).toBe('Edit applied. Re-validate this template’s judge calibration.')
  })

  it('tolerates a missing preview', () => {
    expect(revalidateSummary(null)).toMatch(/Re-validate/)
    expect(revalidateSummary(undefined)).toMatch(/Re-validate/)
  })

  it('names committed effects before consent', () => {
    expect(cascadeConfirmation({ rerun: ['send'], stale: [], skipped: [], committed_effects: ['send'], needs_confirmation: true }))
      .toMatch(/Committed external effects: send/)
  })
})
