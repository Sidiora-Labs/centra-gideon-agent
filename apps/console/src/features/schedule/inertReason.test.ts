import { describe, it, expect } from 'vitest'
import { isInertOutcome, statusMeta } from './scheduleMeta'

describe('isInertOutcome', () => {
  it('covers every member of the backend INERT_OUTCOMES set', () => {
    for (const o of [
      'skipped_overlap',
      'skipped_budget',
      'skipped_gate',
      'skipped_noop',
      'skipped_triage',
      'skipped_missed',
    ]) {
      expect(isInertOutcome(o)).toBe(true)
    }
  })

  it('does NOT treat a real failure as inert', () => {
    for (const o of ['failed', 'failure', 'error', 'timeout', 'blocked_injection', 'refused']) {
      expect(isInertOutcome(o)).toBe(false)
    }
  })

  it('does not treat a successful run as inert', () => {
    for (const o of ['ran', 'ran_late', 'success', 'ok', 'launched', 'deferred']) {
      expect(isInertOutcome(o)).toBe(false)
    }
  })

  it('survives an absent outcome', () => {
    expect(isInertOutcome(undefined)).toBe(false)
    expect(isInertOutcome(null)).toBe(false)
    expect(isInertOutcome('')).toBe(false)
  })

  it('is not fooled by a name that merely contains the word', () => {
    expect(isInertOutcome('was_skipped')).toBe(false)
    expect(isInertOutcome('not_skipped_gate')).toBe(false)
  })
})

describe('the dot and the reason must AGREE', () => {
  const neutral = 'var(--color-on-surface-low)'

  it('a suppression is neutral in BOTH the dot and the reason box', () => {
    for (const o of ['skipped_gate', 'skipped_budget', 'skipped_overlap']) {
      expect(statusMeta(o).tone).toBe(neutral)
      expect(isInertOutcome(o)).toBe(true)
    }
  })

  it('a real failure is danger in BOTH', () => {
    expect(statusMeta('failure').tone).toBe('var(--color-danger)')
    expect(isInertOutcome('failure')).toBe(false)
  })
})
