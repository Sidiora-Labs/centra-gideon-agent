import { describe, it, expect } from 'vitest'
import {
  ITEM_KINDS, NON_CHANNEL_ITEM_KINDS, OPEN_STATUSES,
  kindMeta, statusMeta, isOpen, refTarget, refLabel,
} from './inboxMeta'

describe('item kinds', () => {
  it('falls back to message for an unknown or missing kind', () => {
    expect(kindMeta(undefined).key).toBe('message')
    expect(kindMeta('').key).toBe('message')
    expect(kindMeta('some-future-kind').key).toBe('message')
  })

  it('resolves each declared kind to its own meta', () => {
    for (const k of ITEM_KINDS) expect(kindMeta(k.key).key).toBe(k.key)
  })

  it('gives every kind a label, icon and tone', () => {
    for (const k of ITEM_KINDS) {
      expect(k.label).toBeTruthy()
      expect(k.icon).toBeTruthy()
      expect(k.tone).toMatch(/^var\(--/)
    }
  })

  it('lists non-channel kinds that must not render reply affordances', () => {
    expect(NON_CHANNEL_ITEM_KINDS).toContain('needs_input')
    expect(NON_CHANNEL_ITEM_KINDS).toContain('proposal')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('message')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('mention')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('email')
  })

  it('declares a meta row for every non-channel kind', () => {
    const known = new Set(ITEM_KINDS.map((k) => k.key))
    for (const k of NON_CHANNEL_ITEM_KINDS) expect(known.has(k)).toBe(true)
  })
})

describe('status', () => {
  it('knows seen', () => {
    expect(statusMeta('seen').key).toBe('seen')
    expect(statusMeta('seen').label).toBe('Seen')
  })

  it('treats pending and seen as open, everything else as resolved', () => {
    expect(isOpen('pending')).toBe(true)
    expect(isOpen('seen')).toBe(true)
    expect(isOpen('handled')).toBe(false)
    expect(isOpen('dismissed')).toBe(false)
    expect(isOpen('sent')).toBe(false)
  })

  it('treats a missing status as open', () => {
    expect(isOpen(undefined)).toBe(true)
    expect(isOpen('')).toBe(true)
  })

  it('keeps OPEN_STATUSES and isOpen in agreement', () => {
    for (const s of OPEN_STATUSES) expect(isOpen(s)).toBe(true)
  })

  it('falls back to pending for an unknown status', () => {
    expect(statusMeta('nonsense').key).toBe('pending')
  })
})

describe('refTarget', () => {
  it('returns a BARE path, never a hash — navigate() owns hash mutation', () => {
    const refs: Array<Record<string, string>> = [{ loop: 'L1' }, { session: 's' }, { workflow: 'w' }]
    for (const r of refs) {
      expect(refTarget({ refs: r }).startsWith('#')).toBe(false)
    }
  })

  it('routes a code loop to the code cockpit, not the loops cockpit', () => {
    expect(refTarget({ refs: { loop: 'L1', loop_kind: 'code' } })).toBe('code/L1')
  })

  it('routes a non-code loop to the loops cockpit', () => {
    expect(refTarget({ refs: { loop: 'L1', loop_kind: 'goal' } })).toBe('loops/L1')
    expect(refTarget({ refs: { loop: 'L1' } })).toBe('loops/L1')
  })

  it('routes sessions and workflows', () => {
    expect(refTarget({ refs: { session: 'chat-1' } })).toBe('chat/chat-1')
    expect(refTarget({ refs: { workflow: 'wf-1' } })).toBe('workflows/wf-1')
  })

  it('encodes a session key that needs it', () => {
    expect(refTarget({ refs: { session: 'a/b c' } })).toBe('chat/a%2Fb%20c')
  })

  it('routes an identity-report row to its artifact, and never ahead of an older ref', () => {
    expect(refTarget({ refs: { artifact: 'learning-identity-report' } }))
      .toBe('artifacts/learning-identity-report')
    expect(refTarget({ refs: { artifact: 'a', session: 's1' } })).toBe('chat/s1')
  })

  it('returns empty when there is nowhere to go', () => {
    expect(refTarget({ refs: {} })).toBe('')
    expect(refTarget({})).toBe('')
    expect(refTarget({ refs: { dedup_key: 'k' } })).toBe('')
  })
})

describe('refLabel', () => {
  it('names the referent, not the item kind', () => {
    expect(refLabel({ refs: { loop: 'L1' } })).toBe('Go to loop')
    expect(refLabel({ refs: { session: 's1' } })).toBe('Go to chat')
    expect(refLabel({ refs: { workflow: 'w1' } })).toBe('Go to workflow')
    expect(refLabel({ refs: { artifact: 'learning-identity-report' } })).toBe('Open the report')
  })

  it('falls back to a generic label', () => {
    expect(refLabel({ refs: {} })).toBe('Go to source')
    expect(refLabel({})).toBe('Go to source')
  })

  it('agrees with refTarget about whether there is a destination', () => {
    const cases: Array<{ refs: Record<string, string> }> = [
      { refs: { loop: 'L1' } }, { refs: { session: 's' } }, { refs: {} },
    ]
    for (const c of cases) {
      const hasTarget = refTarget(c) !== ''
      expect(refLabel(c) !== 'Go to source').toBe(hasTarget)
    }
  })
})
