import { describe, expect, it } from 'vitest'
import { coerceInputs, inputFields, labelFor, startsWithoutInput } from './templateStart'
import type { WorkflowInputParam } from '../../shared/data/api'


const P = (over: Partial<WorkflowInputParam> = {}): WorkflowInputParam => ({ type: 'string', ...over })

describe('inputFields', () => {
  it('puts required inputs first', () => {
    const fields = inputFields({
      optional_a: P(),
      required_one: P({ required: true }),
      optional_b: P(),
      required_two: P({ required: true }),
    })
    expect(fields.map((f) => f.name)).toEqual([
      'required_one', 'required_two', 'optional_a', 'optional_b',
    ])
  })

  it('keeps declaration order within each group', () => {
    const fields = inputFields({ zebra: P(), apple: P(), mango: P() })
    expect(fields.map((f) => f.name)).toEqual(['zebra', 'apple', 'mango'])
  })

  it('marks a required field and flags it in the label', () => {
    const [field] = inputFields({ subject: P({ required: true }) })
    expect(field.required).toBe(true)
    expect(field.label).toBe('Subject *')
  })

  it('pre-fills a declared default', () => {
    const [field] = inputFields({ rounds: P({ type: 'number', default: 3 }) })
    expect(field.initial).toBe('3')
  })

  it('leaves initial empty when there is no default', () => {
    const [field] = inputFields({ subject: P({ required: true }) })
    expect(field.initial).toBe('')
  })

  it('renders help text as the placeholder', () => {
    const [field] = inputFields({ q: P({ help: 'The question to answer.' }) })
    expect(field.placeholder).toBe('The question to answer.')
  })

  it('uses a textarea for a long help text', () => {
    const short = inputFields({ a: P({ help: 'short' }) })[0]
    const long = inputFields({ b: P({ help: 'x'.repeat(120) }) })[0]
    expect(short.type).toBe('text')
    expect(long.type).toBe('textarea')
  })

  it('handles no inputs at all', () => {
    expect(inputFields(undefined)).toEqual([])
    expect(inputFields({})).toEqual([])
  })
})

describe('labelFor', () => {
  it('humanizes a snake_case key', () => {
    expect(labelFor('context_root')).toBe('Context root')
    expect(labelFor('verify-command')).toBe('Verify command')
    expect(labelFor('subject')).toBe('Subject')
  })

  it('survives an empty name', () => {
    expect(labelFor('')).toBe('')
  })
})

describe('coerceInputs', () => {
  it('parses a declared number', () => {
    expect(coerceInputs({ rounds: '3' }, { rounds: P({ type: 'number' }) })).toEqual({ rounds: 3 })
  })

  it('passes an unparseable number through verbatim', () => {
    expect(coerceInputs({ rounds: 'many' }, { rounds: P({ type: 'number' }) })).toEqual({ rounds: 'many' })
  })

  it('parses a declared boolean generously', () => {
    const p = { fix: P({ type: 'boolean' }) }
    expect(coerceInputs({ fix: 'true' }, p)).toEqual({ fix: true })
    expect(coerceInputs({ fix: 'yes' }, p)).toEqual({ fix: true })
    expect(coerceInputs({ fix: '1' }, p)).toEqual({ fix: true })
    expect(coerceInputs({ fix: 'no' }, p)).toEqual({ fix: false })
    expect(coerceInputs({ fix: '' }, p)).toEqual({})
  })

  it('DROPS an empty optional answer rather than sending ""', () => {
    const out = coerceInputs(
      { subject: 'a real subject', acceptance: '   ' },
      { subject: P({ required: true }), acceptance: P({ default: 'anything goes' }) },
    )
    expect(out).toEqual({ subject: 'a real subject' })
    expect('acceptance' in out).toBe(false)
  })

  it('keeps an empty REQUIRED answer so the backend can refuse it', () => {
    expect(coerceInputs({ subject: '' }, { subject: P({ required: true }) })).toEqual({ subject: '' })
  })

  it('trims whitespace', () => {
    expect(coerceInputs({ q: '  hello  ' }, { q: P({ required: true }) })).toEqual({ q: 'hello' })
  })

  it('treats an undeclared answer as a string', () => {
    expect(coerceInputs({ extra: 'x' }, undefined)).toEqual({ extra: 'x' })
  })
})

describe('startsWithoutInput', () => {
  it('is true when nothing is required', () => {
    expect(startsWithoutInput(undefined)).toBe(true)
    expect(startsWithoutInput({})).toBe(true)
    expect(startsWithoutInput({ a: P({ default: 'x' }) })).toBe(true)
  })

  it('is false when anything is required', () => {
    expect(startsWithoutInput({ a: P(), b: P({ required: true }) })).toBe(false)
  })
})
