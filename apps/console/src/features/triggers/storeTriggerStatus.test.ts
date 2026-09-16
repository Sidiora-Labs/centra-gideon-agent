import { describe, it, expect } from 'vitest'
import { triggerHealthMeta } from '../schedule/scheduleMeta'

function statusLine(state?: string, enabled = true): string {
  return state === 'autopaused'
    ? 'Stopped by the system after repeated failures'
    : state === 'quarantined'
      ? 'Quarantined — a payload matched an injection pattern; re-author it to resume'
      : state === 'parked'
        ? 'Parked — a resource it needs is busy; it resumes on its own'
        : enabled
          ? 'Firing on its own'
          : 'Paused — it will not fire until re-enabled'
}

const USER_PAUSED = statusLine('paused', false)

describe('the store panel status line', () => {
  it('does NOT describe an autopaused automation as user-paused', () => {
    expect(statusLine('autopaused', false)).not.toBe(USER_PAUSED)
    expect(statusLine('autopaused', false)).toContain('system')
  })

  it('does NOT describe a quarantined automation as user-paused', () => {
    expect(statusLine('quarantined', false)).not.toBe(USER_PAUSED)
    expect(statusLine('quarantined', false)).toContain('re-author')
  })

  it('still describes a USER pause as a user pause', () => {
    expect(statusLine('paused', false)).toBe(USER_PAUSED)
  })

  it('says a parked automation resumes ITSELF', () => {
    expect(statusLine('parked', true)).toContain('resumes on its own')
  })

  it('gives every stopped state a DISTINCT sentence', () => {
    const lines = ['paused', 'autopaused', 'quarantined', 'parked'].map((s) => statusLine(s, false))
    expect(new Set(lines).size).toBe(4)
  })

  it('falls back to the enabled/disabled reading when state is absent', () => {
    expect(statusLine(undefined, true)).toBe('Firing on its own')
    expect(statusLine(undefined, false)).toBe(USER_PAUSED)
  })
})

describe('the status dot beside it', () => {
  it('reuses the shared mapper rather than a third local vocabulary', () => {
    expect(triggerHealthMeta('failing', 'autopaused').tone).toBe('var(--color-danger)')
    expect(triggerHealthMeta('parked', 'parked').tone).toBe('var(--color-info)')
    expect(triggerHealthMeta('ok', 'active').tone).toBe('var(--color-ok)')
  })

  it('keeps a machine stop visually distinct from a user pause', () => {
    expect(triggerHealthMeta('failing', 'autopaused').tone).not.toBe(
      triggerHealthMeta('ok', 'paused').tone,
    )
  })
})
