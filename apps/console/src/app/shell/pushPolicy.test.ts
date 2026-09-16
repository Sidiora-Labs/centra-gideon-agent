import { describe, it, expect } from 'vitest'
import { COMPANION_PATH, deepLinkFor, isPushPayload, notificationFor, shouldFocus, soundMapFromRules, PUSH_CUES } from './pushPolicy'
import { CUES } from '../../shared/theme/soundCues'


describe('the payload gate', () => {
  it('accepts exactly {kind, item_id} of strings', () => {
    expect(isPushPayload({ kind: 'approval', item_id: 'a1' })).toBe(true)
    expect(isPushPayload({ kind: 'approval', item_id: '' })).toBe(true)
  })

  it('rejects a payload carrying a THIRD key', () => {
    expect(isPushPayload({ kind: 'approval', item_id: 'a1', title: 'Run rm -rf /' })).toBe(false)
  })

  it('rejects a missing key, a wrong type, and a non-object', () => {
    expect(isPushPayload({ kind: 'approval' })).toBe(false)
    expect(isPushPayload({ item_id: 'a1' })).toBe(false)
    expect(isPushPayload({ kind: '', item_id: 'a1' })).toBe(false)
    expect(isPushPayload({ kind: 'approval', item_id: 7 })).toBe(false)
    expect(isPushPayload(null)).toBe(false)
    expect(isPushPayload('approval')).toBe(false)
  })
})

describe('the notification the user sees', () => {
  it('composes its words from the KIND, never from the payload', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'SECRET-payroll.csv' })
    expect(note.title).toBe('Approval needed')
    expect(note.body).toBe('A run is waiting for your decision.')
    expect(note.title).not.toContain('SECRET')
    expect(note.body).not.toContain('SECRET')
  })

  it('falls back to fixed generic copy rather than painting an unknown kind', () => {
    const note = notificationFor({ kind: 'not-a-real-kind-<script>', item_id: 'x' })
    expect(note.title).toBe('Gideon')
    expect(note.title).not.toContain('script')
    expect(note.body).not.toContain('not-a-real-kind')
  })

  it('coalesces per ITEM, so a retry replaces but a second approval does not', () => {
    const first = notificationFor({ kind: 'approval', item_id: 'a1' })
    const retry = notificationFor({ kind: 'approval', item_id: 'a1' })
    const other = notificationFor({ kind: 'approval', item_id: 'a2' })
    expect(retry.tag).toBe(first.tag)
    expect(other.tag).not.toBe(first.tag)
  })

  it('makes an approval require interaction and nothing else does', () => {
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }).requireInteraction).toBe(true)
    expect(notificationFor({ kind: 'inbox_alert', item_id: 'i1' }).requireInteraction).toBe(false)
  })
})

describe('the deep link', () => {
  it('addresses the specific approval card', () => {
    expect(deepLinkFor('approval', 'ap-1')).toBe('/#/companion?approval=ap-1')
  })

  it('percent-encodes the id', () => {
    expect(deepLinkFor('approval', 'a&b=c')).toBe('/#/companion?approval=a%26b%3Dc')
  })

  it('falls back to the bare companion route when there is nothing to address', () => {
    expect(deepLinkFor('approval', '')).toBe(COMPANION_PATH)
    expect(deepLinkFor('inbox_alert', 'i1')).toBe(COMPANION_PATH)
  })
})

describe('client reuse on click', () => {
  it('focuses an existing same-origin window instead of opening another', () => {
    expect(shouldFocus('https://gw.example/#/companion?approval=a', 'https://gw.example')).toBe(true)
    expect(shouldFocus('https://gw.example/#/settings', 'https://gw.example')).toBe(true)
  })

  it('refuses a different origin and an unparseable url', () => {
    expect(shouldFocus('https://evil.example/#/companion', 'https://gw.example')).toBe(false)
    expect(shouldFocus('not a url', 'https://gw.example')).toBe(false)
  })
})


describe('the closed push voice set', () => {
  it('matches soundCues CUES exactly — the two must not drift', () => {
    expect([...PUSH_CUES].sort()).toEqual(Object.keys(CUES).sort())
  })
})

describe('notificationFor resolves the voice from the rules, not the wire or a fixed table', () => {
  it('plays the voice the rules configured for that kind', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'a1' }, { approval: 'coin_blip' })
    expect(note.sound).toBe('coin_blip')
  })

  it('is silent when the kind has no configured voice — absent is the default', () => {
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }, {}).sound).toBeUndefined()
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }).sound).toBeUndefined()
  })

  it('drops an unknown voice rather than handing the client an unplayable one', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'a1' }, { approval: 'ka-ching' })
    expect(note.sound).toBeUndefined()
  })

  it('reads the voice per kind while the WORDS still come from the fixed table', () => {
    const note = notificationFor({ kind: 'inbox_alert', item_id: 'i1' }, { inbox_alert: 'terminal_bell' })
    expect(note.sound).toBe('terminal_bell')
    expect(note.title).toBe('Inbox alert')
  })
})

describe('soundMapFromRules', () => {
  it('keys the map by WIRE kind and keeps only known, present voices', () => {
    const doc = {
      rules: [
        { key: 'approval/requested', wire: 'approval', sound: 'coin_blip' },
        { key: 'inbox/alert', wire: 'inbox_alert', sound: null },
        { key: 'x/y', wire: 'xy', sound: 'ka-ching' },
        { key: 'z/z', sound: 'error' },
      ],
    }
    expect(soundMapFromRules(doc)).toEqual({ approval: 'coin_blip' })
  })

  it('is defensive against a malformed document — degrades to no cue', () => {
    expect(soundMapFromRules(null)).toEqual({})
    expect(soundMapFromRules({})).toEqual({})
    expect(soundMapFromRules({ rules: 'nope' })).toEqual({})
  })
})


it('treats inherited object names as unknown kinds and ignores invalid rule rows', () => {
  for (const kind of ['constructor', '__proto__', 'toString']) {
    expect(notificationFor({ kind, item_id: 'x' }).title).toBe('Gideon')
  }
  expect(soundMapFromRules({ rules: [null, 3, [], { wire: 'approval', sound: 'error' }] })).toEqual({ approval: 'error' })
})
