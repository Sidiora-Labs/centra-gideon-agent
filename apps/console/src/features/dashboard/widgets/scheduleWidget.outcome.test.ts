import { describe, it, expect } from 'vitest'
import { statusMeta } from '../../schedule/scheduleMeta'

describe('the Schedule widget outcome mapping', () => {
  it('does NOT render a suppressed fire as "ran"', () => {
    const m = statusMeta('skipped_gate')
    expect(m.label).not.toBe('ran')
    expect(m.label).toBe('gate')
  })

  it('gives a suppression a NEUTRAL tone, not ok-green and not danger', () => {
    const m = statusMeta('skipped_gate')
    expect(m.tone).toBe('var(--color-on-surface-low)')
  })

  it('still renders a real success and a real failure distinctly', () => {
    expect(statusMeta('success').tone).toBe('var(--color-ok)')
    expect(statusMeta('failure').tone).toBe('var(--color-danger)')
  })

  it('renders the typed RAN outcome the projection actually emits', () => {
    expect(statusMeta('ran').label).not.toBe('never run')
  })

  it('keeps a blocked payload distinguishable from a skip', () => {
    expect(statusMeta('blocked_injection').label).toBe('blocked')
    expect(statusMeta('blocked_injection').tone).toBe('var(--color-danger)')
    expect(statusMeta('skipped_gate').tone).not.toBe(statusMeta('blocked_injection').tone)
  })

  it('falls back to `status` when a row has no typed outcome', () => {
    expect(statusMeta(undefined as unknown as string).label).toBe('never run')
    expect(statusMeta('success').label).toBe('ok')
  })
})

describe('the completeness guard this pattern earned', () => {
  const OUTCOMES = [
    'ran', 'ran_late', 'skipped_overlap', 'skipped_budget', 'skipped_gate', 'skipped_noop',
    'skipped_triage', 'skipped_missed', 'deferred', 'refused', 'blocked_injection', 'failed',
  ]

  it('renders EVERY typed outcome as something other than "never run"', () => {
    const unmapped = OUTCOMES.filter((o) => statusMeta(o).label === 'never run')
    expect(unmapped).toEqual([])
  })

  it('keeps a FAILED fire visually distinct from a never-run one', () => {
    expect(statusMeta('failed').tone).toBe('var(--color-danger)')
    expect(statusMeta(undefined as unknown as string).tone).not.toBe('var(--color-danger)')
  })
})
