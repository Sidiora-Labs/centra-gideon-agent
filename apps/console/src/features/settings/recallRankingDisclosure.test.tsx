import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import type { RecallRankingDisclosure } from '../../shared/data/api'
import { RecallRankingDisclosureView } from './MemoryPanel'

const disclosure: RecallRankingDisclosure = {
  method: 'separate_memory_rankings',
  summary: 'Facts and episodes are ranked separately, then presented in those groups.',
  score: {
    label: 'Ranking score',
    kind: 'relative_ordering_signal',
    value: null,
    shown: false,
    is_probability: false,
    comparable_across_queries: false,
    explanation: 'A relative ordering signal for this query, not confidence, probability, or a percentage.',
  },
  signals: [
    { id: 'keyword', label: 'Keyword match', active: true, applies_to: ['facts', 'episodes'], detail: 'Matches query words.' },
    { id: 'vector', label: 'Meaning match', active: false, applies_to: ['facts', 'episodes'], detail: 'Matches meaning with embeddings.' },
  ],
}

describe('recall ranking disclosure', () => {
  it('uses the API vocabulary and does not present an internal score as confidence', () => {
    const text = render(<RecallRankingDisclosureView disclosure={disclosure} />).container.textContent ?? ''
    expect(text).toContain('Facts and episodes are ranked separately')
    expect(text).toContain('Ranking score: not shown for this combined recall')
    expect(text).toContain('not confidence, probability, or a percentage')
    expect(text).toContain('Keyword match · facts + episodes')
    expect(text).toContain('Meaning match (unavailable)')
  })

  it('shows a supplied ranking score as a raw ordering value, never a percentage', () => {
    const scored = { ...disclosure, score: { ...disclosure.score, shown: true, value: 0.0328 } }
    const text = render(<RecallRankingDisclosureView disclosure={scored} />).container.textContent ?? ''
    expect(text).toContain('Ranking score: 0.0328')
    expect(text).not.toContain('3%')
  })
})
