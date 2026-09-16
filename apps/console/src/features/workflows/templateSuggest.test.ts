import { describe, expect, it } from 'vitest'
import { availableSuggestions, intentKind, suggestTemplate } from './templateSuggest'


const SHIPPED = new Set([
  'general-project', 'goal-pursuit-open-ended', 'goal-pursuit-verifiable',
  'code-project', 'design-project', 'deep-research',
])

describe('intentKind', () => {
  it('reads a coding intent as the code kind', () => {
    expect(intentKind('fix the login bug')).toBe('code')
    expect(intentKind('implement a new API endpoint')).toBe('code')
    expect(intentKind('refactor the parser')).toBe('code')
  })

  it('reads a research intent as the research kind', () => {
    expect(intentKind('research the best vector databases')).toBe('research')
    expect(intentKind('investigate and compare the options with sources')).toBe('research')
  })

  it('reads a design intent as the design kind', () => {
    expect(intentKind('design a settings UI mockup')).toBe('design')
  })

  it('returns empty when nothing matches, rather than defaulting', () => {
    expect(intentKind('')).toBe('')
    expect(intentKind('   ')).toBe('')
    expect(intentKind('the quick brown fox')).toBe('')
  })

  it('matches whole words only', () => {
    expect(intentKind('scode alembic')).toBe('')
  })

  it('lets the action verb win over its subject on a tie', () => {
    expect(intentKind('refactor the research pipeline')).toBe('code')
  })
})

describe('suggestTemplate', () => {
  it('suggests code-project for a coding intent (criterion 11)', () => {
    expect(suggestTemplate('fix the failing test', SHIPPED)).toBe('code-project')
  })

  it('suggests deep-research for a research intent', () => {
    expect(suggestTemplate('research the tradeoffs', SHIPPED)).toBe('deep-research')
  })

  it('drops a suggestion for a template that is not available', () => {
    expect(suggestTemplate('research the tradeoffs', new Set(['general-project']))).toBe('')
  })

  it('returns empty for an unrecognizable intent', () => {
    expect(suggestTemplate('hello there', SHIPPED)).toBe('')
    expect(suggestTemplate('', SHIPPED)).toBe('')
  })

  it('accepts an array as well as a set for the available templates', () => {
    expect(suggestTemplate('research it', [...SHIPPED])).toBe('deep-research')
  })
})

describe('availableSuggestions', () => {
  it('lists only kind→template pairs whose template ships', () => {
    const pairs = availableSuggestions(SHIPPED)
    const byKind = Object.fromEntries(pairs.map((p) => [p.kind, p.template]))
    expect(byKind.code).toBe('code-project')
    expect(byKind.research).toBe('deep-research')
    expect(byKind.design).toBe('design-project')
  })

  it('omits a kind whose template is not installed', () => {
    const pairs = availableSuggestions(new Set(['deep-research']))
    expect(pairs).toEqual([{ kind: 'research', template: 'deep-research' }])
  })
})
