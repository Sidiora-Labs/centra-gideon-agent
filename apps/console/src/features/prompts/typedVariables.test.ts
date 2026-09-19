import { describe, expect, it } from 'vitest'
import { detectInlineVariables, detectPlaceholders, promptVars, variableTypeLabel } from './promptMeta'
import { selectPromptRows, type PromptLibraryRow } from './promptLibraryState'

describe('typed inline prompt variables', () => {
  it('mirrors the engine type aliases and option grammar', () => {
    expect(detectInlineVariables([
      '{{ count::int }}',
      '{{ notes::long_text }}',
      '{{ enabled::bool }}',
      '{{ mode::select::[fast, careful] }}',
      '{{ color::[red, green, ] }}',
      '{{ fallback::unknown }}',
    ].join('\n'))).toEqual([
      { name: 'count', type: 'number' },
      { name: 'notes', type: 'textarea' },
      { name: 'enabled', type: 'boolean' },
      { name: 'mode', type: 'select', options: ['fast', 'careful'] },
      { name: 'color', type: 'select', options: ['red', 'green'] },
      { name: 'fallback', type: 'text' },
    ])
  })

  it('excludes bare, dotted, include, and expression forms and keeps the first declaration', () => {
    const content = [
      '{{ bare }}',
      '{{ person.name::text }}',
      '{{> shared }}',
      '{{ upper(name::text) }}',
      '{{ mode::enum::[one, two] }}',
      '{{ mode::number }}',
      '{{ total::float }}',
    ].join('\n')
    expect(detectInlineVariables(content)).toEqual([
      { name: 'mode', type: 'select', options: ['one', 'two'] },
      { name: 'total', type: 'number' },
    ])
    expect(detectPlaceholders(content)).toEqual(['bare', 'mode', 'total'])
  })

  it('preserves explicit declarations while appending inferred variables', () => {
    expect(promptVars({
      content: '{{ mode::select::[fast, safe] }} {{ retries::int }}',
      variables: [{ name: 'mode', type: 'text', required: true }],
    })).toEqual([
      { name: 'mode', type: 'text', required: true },
      { name: 'retries', type: 'number' },
    ])
  })

  it('uses human labels and inferred variables when sorting variable counts', () => {
    expect(variableTypeLabel('textarea')).toBe('Text (block)')
    expect(variableTypeLabel('select')).toBe('Choice')
    const rows = [
      { name: 'one', content: '{{ a::text }}' },
      { name: 'two', content: '{{ a::text }} {{ b::number }}' },
    ] as PromptLibraryRow[]
    expect(selectPromptRows(rows, '', 'vars', 'all').map(row => row.name)).toEqual(['two', 'one'])
  })
})
