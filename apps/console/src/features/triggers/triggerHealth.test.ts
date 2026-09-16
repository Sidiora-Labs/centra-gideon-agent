import { describe, it, expect } from 'vitest'
import { triggerHealthMeta } from '../schedule/scheduleMeta'

const HEALTH = ['ok', 'degraded', 'parked', 'failing']
const STATES = ['active', 'paused', 'autopaused', 'parked', 'quarantined', 'retired']

describe('triggerHealthMeta — the health rollup', () => {
  it('does NOT render a failing automation as a neutral dot', () => {
    const m = triggerHealthMeta('failing', 'active')
    expect(m.label).toBe('failing')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('keeps failing, degraded and parked visually DISTINCT', () => {
    const tones = new Set(
      ['failing', 'degraded', 'parked'].map((h) => triggerHealthMeta(h, 'active').tone),
    )
    expect(tones.size).toBe(3)
  })

  it('gives PARKED an informational tone, not a danger one', () => {
    expect(triggerHealthMeta('parked', 'active').tone).toBe('var(--color-info)')
  })

  it('renders every health value as something, never a blank label', () => {
    const blank = HEALTH.filter((h) => !triggerHealthMeta(h, 'active').label)
    expect(blank).toEqual([])
  })
})

describe('triggerHealthMeta — the lifecycle state', () => {
  it('lets a STOPPED state outrank the health rollup', () => {
    expect(triggerHealthMeta('failing', 'autopaused').label).toBe('autopaused')
    expect(triggerHealthMeta('failing', 'quarantined').label).toBe('quarantined')
  })

  it('does not let a healthy rollup hide a stopped automation', () => {
    expect(triggerHealthMeta('ok', 'autopaused').label).toBe('autopaused')
    expect(triggerHealthMeta('ok', 'paused').label).toBe('paused')
  })

  it('leaves an ACTIVE trigger reporting its health', () => {
    expect(triggerHealthMeta('ok', 'active').label).toBe('ok')
    expect(triggerHealthMeta('degraded', 'active').label).toBe('degraded')
  })

  it('renders every lifecycle state as something distinguishable', () => {
    const blank = STATES.filter((s) => s !== 'active' && !triggerHealthMeta('ok', s).label)
    expect(blank).toEqual([])
  })

  it('keeps quarantined distinct from a mere pause', () => {
    expect(triggerHealthMeta('failing', 'quarantined').tone).toBe('var(--color-danger)')
    expect(triggerHealthMeta('failing', 'paused').tone).not.toBe('var(--color-danger)')
  })
})

describe('the wire contract this depends on', () => {
  it('the store projection must emit `state`, not only `health`', () => {
    expect(triggerHealthMeta('failing', 'autopaused')).not.toEqual(
      triggerHealthMeta('failing', 'active'),
    )
  })
})
