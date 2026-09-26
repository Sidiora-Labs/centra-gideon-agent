import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { KnowledgeContextCard, KnowledgeContextResult } from '../../shared/data/api'
import { KnowledgeCitation, KnowledgeRetrievalChunks, KnowledgeWebSearch } from './auiKnowledgeResults'

const source: KnowledgeContextCard = {
  id: 'persisted/source-1', title: 'Incident review', provider: 'native', source_type: 'document',
  tokens: 42, summary: 'Recovery was observed.', content: 'The worker recovered after the restart.',
  section: 'Timeline', line_range: [18, 24], deep_link: 'https://docs.example.org/incident',
}

function result(...results: KnowledgeContextCard[]): KnowledgeContextResult {
  return { query: 'worker recovery', results, total_tokens: results.reduce((sum, card) => sum + card.tokens, 0), max_tokens: 4000 }
}

describe('live knowledge citations use the adopted preview primitive', () => {
  it('keeps one ordered donor citation cluster beside real web search rows', async () => {
    render(<KnowledgeWebSearch result={result(source,
      { ...source, id: 'persisted/source-2', title: 'Follow-up', summary: 'The second source.', deep_link: null },
    )} />)
    const cluster = screen.getByLabelText('Knowledge citations')
    expect(cluster).toHaveAttribute('data-slot', 'inline-citation')
    expect(within(cluster).getAllByRole('button').map((button) => button.textContent)).toEqual(['1', '2'])
    expect(screen.getByText('2 results')).toBeInTheDocument()
    expect(screen.getByText('Recovery was observed.')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Knowledge citations')).toHaveLength(1)

    await userEvent.hover(within(cluster).getByRole('button', { name: '1' }))
    expect(await screen.findByText('Incident review, Timeline')).toBeInTheDocument()
    expect(screen.getAllByText('docs.example.org').length).toBeGreaterThan(1)
    expect(screen.getAllByText('Recovery was observed.').length).toBeGreaterThan(1)
  })

  it('opens the exact selected source once and preserves result numbering', () => {
    const onOpen = vi.fn()
    render(<KnowledgeWebSearch result={result(source,
      { ...source, id: 'persisted/source-2', title: 'Follow-up', section: null },
    )} onOpen={onOpen} />)
    const cluster = screen.getByLabelText('Knowledge citations')
    fireEvent.click(within(cluster).getByRole('button', { name: 'Open source Follow-up' }))
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('persisted/source-2')
    expect(within(cluster).getByRole('button', { name: 'Open source Follow-up' })).toHaveTextContent('2')
  })

  it('uses real provider metadata and passage fallback with no clickable source without a handler', async () => {
    render(<KnowledgeRetrievalChunks result={result({
      ...source, deep_link: 'javascript:alert(1)', summary: undefined,
    })} />)
    const cluster = screen.getByLabelText('Knowledge citations')
    const trigger = within(cluster).getByRole('button', { name: '1' })
    expect(within(cluster).queryByRole('button', { name: /Open source/ })).toBeNull()
    await userEvent.hover(trigger)
    expect(await screen.findByText('Incident review, Timeline')).toBeInTheDocument()
    expect(screen.getByText('native')).toBeInTheDocument()
    expect(screen.getAllByText('The worker recovered after the restart.').length).toBeGreaterThan(1)
    expect(screen.queryByText('javascript:alert(1)')).toBeNull()
  })

  it('keeps absent source metadata absent and never invents a snippet', async () => {
    render(<KnowledgeCitation card={{ ...source, summary: undefined, content: undefined,
      deep_link: null, provider: undefined, source_type: null, section: null }} index={4} />)
    const trigger = screen.getByRole('button', { name: '5' })
    await userEvent.hover(trigger)
    expect((await screen.findAllByText('Incident review')).length).toBeGreaterThan(1)
    expect(screen.queryByText('Recovery was observed.')).toBeNull()
    expect(screen.queryByRole('button', { name: /Open source/ })).toBeNull()
  })

  it('uses an actual safe host ahead of provider and preserves the persisted ID', async () => {
    const onOpen = vi.fn()
    render(<KnowledgeCitation card={source} onOpen={onOpen} index={6} />)
    const trigger = screen.getByRole('button', { name: 'Open source Incident review, Timeline' })
    expect(trigger).toHaveTextContent('7')
    await userEvent.hover(trigger)
    expect(await screen.findByText('docs.example.org')).toBeInTheDocument()
    expect(screen.queryByText('native')).toBeNull()
    fireEvent.click(trigger)
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('persisted/source-1')
  })
})
