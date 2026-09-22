import { describe, it, expect } from 'vitest'
import { toneChipBg, kindMeta } from './notificationMeta'


describe('notificationMeta shared visual helpers', () => {
  it('toneChipBg: the exact 16% tint over transparent', () => {
    expect(toneChipBg('var(--color-warn)')).toBe('color-mix(in srgb, var(--color-warn) 16%, transparent)')
  })

  it('kindMeta tones are token-routed (CSS vars, never raw hex)', () => {
    for (const kind of ['info', 'error', 'success', 'unknown-kind']) {
      expect(kindMeta(kind).tone).toMatch(/^var\(--color-/)
    }
  })
})

const BACKEND_KINDS = [
  'agent_request', 'alert', 'complete', 'digest', 'error', 'failed', 'fired', 'generic',
  'info', 'message', 'needs_input', 'progress', 'proposal', 'result', 'retire',
  'route_drift', 'session', 'status', 'subagent', 'success', 'update', 'warning',
  'agent', 'app.route.drift', 'app_update', 'cron', 'feedback_retire', 'heartbeat', 'hook',
  'inbox_alert', 'loop', 'schedule',
]

describe('kindMeta covers every kind the backend emits', () => {
  it('no backend kind renders as its raw lowercase key', () => {
    const leaked = BACKEND_KINDS.filter((k) => kindMeta(k).label === k)
    expect(leaked).toEqual([])
  })

  it('proposal uses the registry\'s declared display name', () => {
    expect(kindMeta('proposal').label).toBe('Skill proposal')
  })

  it('retired stalled display key uses the unknown-kind fallback', () => {
    expect(kindMeta('stalled').label).toBe('stalled')
  })

  it('every backend kind yields a usable label / icon / token-routed tone', () => {
    for (const kind of BACKEND_KINDS) {
      const km = kindMeta(kind)
      expect(km.label.length, kind).toBeGreaterThan(0)
      expect(km.icon, kind).toBeDefined()
      expect(km.tone, kind).toMatch(/^var\(--color-/)
    }
  })

  it('a genuinely unknown kind still hits the fallback with a usable KindMeta', () => {
    const km = kindMeta('zzz_not_a_kind')
    expect(km.label).toBe('zzz_not_a_kind')
    expect(km.icon).toBeDefined()
    expect(km.tone).toMatch(/^var\(--color-/)
    expect(kindMeta('').label).toBe('Notification')
  })
})
