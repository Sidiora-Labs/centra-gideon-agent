import { describe, it, expect } from 'vitest'
import { serializeJsonField, parseJsonField } from './appConfigForm'


describe('serializeJsonField', () => {
  it('pretty-prints an array value', () => {
    expect(serializeJsonField([{ channel_id: 'C1', name: 'x' }], 'array'))
      .toBe('[\n  {\n    "channel_id": "C1",\n    "name": "x"\n  }\n]')
  })

  it('renders empty containers for null/undefined (never "[object Object]")', () => {
    expect(serializeJsonField(undefined, 'array')).toBe('[]')
    expect(serializeJsonField(null, 'object')).toBe('{}')
    expect(serializeJsonField({}, 'object')).toBe('{}')
  })

  it('never emits the [object Object] stringification bug', () => {
    expect(serializeJsonField({ a: 1 }, 'object')).not.toContain('[object Object]')
  })
})

describe('parseJsonField', () => {
  it('parses valid array JSON', () => {
    expect(parseJsonField('["C_A", "C_B"]', 'array')).toEqual({ value: ['C_A', 'C_B'] })
  })

  it('parses valid object JSON', () => {
    expect(parseJsonField('{"C1": {"activation": "mention"}}', 'object'))
      .toEqual({ value: { C1: { activation: 'mention' } } })
  })

  it('empty text clears to the empty container', () => {
    expect(parseJsonField('   ', 'array')).toEqual({ value: [] })
    expect(parseJsonField('', 'object')).toEqual({ value: {} })
  })

  it('rejects malformed JSON with an inline error (no corrupt value)', () => {
    const r = parseJsonField('[not json', 'array')
    expect(r).toEqual({ error: 'invalid JSON' })
  })

  it('rejects a wrong-shape value (array where object expected and vice versa)', () => {
    expect(parseJsonField('[]', 'object')).toEqual({ error: 'must be a JSON object' })
    expect(parseJsonField('{}', 'array')).toEqual({ error: 'must be a JSON array' })
    expect(parseJsonField('42', 'array')).toEqual({ error: 'must be a JSON array' })
    expect(parseJsonField('null', 'object')).toEqual({ error: 'must be a JSON object' })
  })
})
