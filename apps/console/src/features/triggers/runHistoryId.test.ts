import { describe, it, expect } from 'vitest'

const rawOf = (triggerId: string) => triggerId.replace(/^(?:schedule|store|lifecycle|event):/, '')

describe('the facade id vs the run-store key', () => {
  it('strips only the FACADE prefix, leaving a store kind intact', () => {
    expect(rawOf('store:file:notes')).toBe('file:notes')
    expect(rawOf('store:web_watch:feed')).toBe('web_watch:feed')
  })

  it('reduces a schedule id to its bare job id', () => {
    expect(rawOf('schedule:abc')).toBe('abc')
  })

  it('leaves a bare id untouched', () => {
    expect(rawOf('abc')).toBe('abc')
  })

  it('strips exactly one prefix, not every colon segment', () => {
    expect(rawOf('store:file:a:b')).toBe('file:a:b')
  })
})

describe('the endpoint id', () => {
  const url = (triggerId: string, limit = 10, offset = 0) =>
    `/api/triggers/${encodeURIComponent(triggerId)}/history?limit=${limit}&offset=${offset}`

  it('addresses a store trigger, which the old wrapper could not', () => {
    expect(url('store:file:notes')).toContain('store%3Afile%3Anotes')
    expect(url('store:file:notes')).not.toContain('schedule')
  })

  it('still addresses a schedule trigger', () => {
    expect(url('schedule:abc')).toContain('schedule%3Aabc')
  })
})
