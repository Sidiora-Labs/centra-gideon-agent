import { describe, it, expect } from 'vitest'
import { statusMeta } from './scheduleMeta'

describe('statusMeta — the typed outcome vocabulary', () => {
  it('does not render a BLOCKED payload as "never run"', () => {
    const m = statusMeta('blocked_injection')
    expect(m.label).toBe('blocked')
    expect(m.label).not.toBe('never run')
  })

  it('gives a blocked payload a DANGER tone', () => {
    expect(statusMeta('blocked_injection').tone).toBe('var(--color-danger)')
  })

  it('renders a suppressed fire neutrally, not as an error', () => {
    const m = statusMeta('skipped_gate')
    expect(m.label).toBe('gate')
    expect(m.tone).toBe('var(--color-on-surface-low)')
  })

  it('humanises every skipped_* variant', () => {
    expect(statusMeta('skipped_overlap').label).toBe('overlap')
    expect(statusMeta('skipped_budget').label).toBe('budget')
    expect(statusMeta('skipped_noop').label).toBe('noop')
    expect(statusMeta('skipped_triage').label).toBe('triage')
  })

  it('renders deferred as postponed, not failed', () => {
    const m = statusMeta('deferred')
    expect(m.label).toBe('deferred')
    expect(m.tone).toBe('var(--color-info)')
  })

  it('renders a policy refusal distinctly from a failure', () => {
    const m = statusMeta('refused')
    expect(m.label).toBe('refused')
    expect(m.tone).not.toBe('var(--color-danger)')
  })

  it('keeps the PRE-EXISTING labels unchanged', () => {
    expect(statusMeta('ok').label).toBe('ok')
    expect(statusMeta('success').label).toBe('ok')
    expect(statusMeta('error').label).toBe('error')
    expect(statusMeta('failure').label).toBe('error')
    expect(statusMeta('timeout').label).toBe('timed out')
    expect(statusMeta('launched').label).toBe('launched')
  })

  it('still says "never run" for a genuinely unrun trigger', () => {
    expect(statusMeta(null).label).toBe('never run')
    expect(statusMeta('').label).toBe('never run')
    expect(statusMeta(undefined).label).toBe('never run')
  })

  it('does not mistake an unknown outcome for a skip', () => {
    expect(statusMeta('was_skipped').label).toBe('never run')
  })
})
