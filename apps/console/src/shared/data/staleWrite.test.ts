import { describe, expect, it } from 'vitest'
import {
  basedOn, differencePatch, isStaleWrite, mergeRecord, mergeText, presentSecrets,
  rebaseList, sameDocument,
} from './staleWrite'

describe('stale document revisions', () => {
  it('quotes the exact base for If-Match and recognizes typed refusals', () => {
    expect(basedOn('r1')).toEqual({ 'If-Match': '"r1"' })
    expect(isStaleWrite(Object.assign(new Error('changed'), { code: 'stale_write' }))).toBe(true)
    expect(isStaleWrite(Object.assign(new Error('missing'), { code: 'revision_required' }))).toBe(true)
    expect(isStaleWrite(Object.assign(new Error('bad request'), { code: 'bad_request' }))).toBe(false)
  })

  it('merges independent text edits and refuses overlapping edits', () => {
    const base = 'one\ntwo\nthree'
    expect(mergeText(base, 'ONE\ntwo\nthree', 'one\ntwo\nTHREE')).toBe('ONE\ntwo\nTHREE')
    expect(mergeText(base, 'one\nMINE\nthree', 'one\nTHEIRS\nthree')).toBeNull()
  })

  it('keeps independent record fields and set-like list changes', () => {
    const base = { name: 'old', model: 'a', tags: ['one', 'two'] }
    expect(mergeRecord(base, { ...base, model: 'b' }, { ...base, name: 'new' }))
      .toEqual({ name: 'new', model: 'b', tags: ['one', 'two'] })
    expect(rebaseList(['one', 'two'], ['one', 'three'])(['one', 'two', 'four']))
      .toEqual(['one', 'four', 'three'])
  })

  it('compares canonical documents and hides stored or newly entered secrets in review', () => {
    expect(sameDocument({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true)
    expect(differencePatch({ a: 1 }, { a: 2 })).toContain('+  "a": 2')
    expect(presentSecrets((key) => key === 'token', ['token'])({ token: '', endpoint: 'local' }))
      .toEqual({ token: '(saved, unchanged)', endpoint: 'local' })
    expect(presentSecrets((key) => key === 'token', [])({ token: 'private' }).token)
      .toBe('(new value, hidden)')
  })
})
