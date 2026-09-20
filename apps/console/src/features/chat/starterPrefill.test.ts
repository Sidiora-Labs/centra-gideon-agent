import { describe, expect, it } from 'vitest'
import type { SessionTemplate } from '../../shared/data/api'
import { starterPrefill } from './starterPrefill'

const STARTERS: SessionTemplate[] = [
  { id: 'research', name: 'Research', agent: 'scout', model: 'sonnet', reasoning_effort: 'high', first_prompt: 'Investigate this topic:', created_at: 1 },
  { id: 'plain', name: 'Plain chat', agent: '', model: '', reasoning_effort: '', first_prompt: '', created_at: 2 },
]

describe('starterPrefill', () => {
  it('maps a starter ID to its composer prefill', () => {
    expect(starterPrefill('research', STARTERS)).toEqual({
      name: 'Research',
      selection: { agent: 'scout', model: 'sonnet', reasoning: 'high' },
      input: 'Investigate this topic:',
    })
    expect(starterPrefill('plain', STARTERS)).toEqual({ name: 'Plain chat', selection: {}, input: '' })
    expect(starterPrefill('missing', STARTERS)).toBeNull()
  })
})
