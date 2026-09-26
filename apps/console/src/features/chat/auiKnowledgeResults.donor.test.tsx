import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type KnowledgeContextCard, type KnowledgeContextResult } from '../../shared/data/api'
import { AuiKnowledgePanel } from '../knowledge/auiKnowledgePanel'
import { KnowledgeRetrievalChunks, KnowledgeWebSearch } from './auiKnowledgeResults'

afterEach(() => vi.restoreAllMocks())

const card: KnowledgeContextCard = {
  id: 'source-17',
  title: 'Incident review',
  provider: 'native',
  tokens: 42,
  summary: 'Recovery was observed.',
  content: 'The worker recovered after the restart.',
  source_type: 'document',
  section: 'Timeline',
  line_range: [18, 24],
  deep_link: 'https://docs.example.org/incident',
}

function result(...results: KnowledgeContextCard[]): KnowledgeContextResult {
  return { query: 'worker recovery', results, total_tokens: results.reduce((sum, item) => sum + item.tokens, 0), max_tokens: 4000 }
}

function donor(slot: 'web-search' | 'retrieval-chunks') {
  const nodes = document.querySelectorAll(`[data-slot="${slot}"]`)
  expect(nodes).toHaveLength(2)
  return nodes[1] as HTMLElement
}

describe('real donor knowledge result surfaces', () => {
  it('renders the donor web query, count, real summary, safe URL and citation', () => {
    render(<KnowledgeWebSearch result={result(card)} />)
    const region = screen.getByRole('region', { name: 'Knowledge search results for worker recovery' })
    expect(region).toContainElement(donor('web-search'))
    expect(within(donor('web-search')).getByText('worker recovery')).toBeInTheDocument()
    expect(within(donor('web-search')).getByText('1 results')).toBeInTheDocument()
    expect(within(donor('web-search')).getByText('Recovery was observed.')).toBeInTheDocument()
    expect(within(donor('web-search')).getByText('docs.example.org')).toBeInTheDocument()
    const link = within(donor('web-search')).getByRole('link')
    expect(link).toHaveAttribute('href', 'https://docs.example.org/incident')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('Incident review, Timeline')).toBeInTheDocument()
    expect(within(donor('web-search')).queryByRole('button')).toBeNull()
  })

  it('routes a donor web row and its citation through the real source ID', () => {
    const onOpen = vi.fn()
    render(<KnowledgeWebSearch result={result(card)} onOpen={onOpen} />)
    fireEvent.click(within(donor('web-search')).getByRole('button', { name: /Incident review/ }))
    fireEvent.click(within(screen.getByLabelText('Knowledge citations')).getByRole('button', { name: 'Open source Incident review, Timeline' }))
    expect(onOpen.mock.calls).toEqual([['source-17'], ['source-17']])
  })

  it('keeps an unsafe deep link as inert source text while retaining real provider metadata', () => {
    render(<KnowledgeWebSearch result={result({ ...card, deep_link: 'javascript:alert(1)', summary: undefined })} />)
    const surface = donor('web-search')
    expect(within(surface).getByText('native')).toBeInTheDocument()
    expect(within(surface).queryByRole('link')).toBeNull()
    expect(within(surface).queryByRole('button')).toBeNull()
    expect(screen.getByText('Incident review, Timeline')).toBeInTheDocument()
  })

  it('uses recorded source type when no host or provider exists, including an empty metadata case', () => {
    render(<KnowledgeWebSearch result={result(
      { ...card, id: 'typed-source', provider: undefined, deep_link: null, source_type: 'bookmark' },
      { ...card, id: 'untyped-source', title: 'Untyped source', provider: undefined, deep_link: null, source_type: null },
    )} />)
    expect(within(donor('web-search')).getByText('bookmark')).toBeInTheDocument()
    expect(within(donor('web-search')).getByText('2 results')).toBeInTheDocument()
    expect(within(donor('web-search')).getByText('Untyped source')).toBeInTheDocument()
  })

  it('renders the donor passage with real section, text and tokens without a fabricated score', () => {
    render(<KnowledgeRetrievalChunks result={result(card)} />)
    const region = screen.getByRole('region', { name: 'Retrieved passages' })
    expect(region).toContainElement(donor('retrieval-chunks'))
    expect(within(donor('retrieval-chunks')).getByText('Timeline')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).getByText('The worker recovered after the restart.')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).getByText('42 tokens')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).queryByRole('meter')).toBeNull()
    expect(within(donor('retrieval-chunks')).queryByRole('button')).toBeNull()
  })

  it('routes the donor passage and citation through the same persisted source ID', () => {
    const onOpen = vi.fn()
    render(<KnowledgeRetrievalChunks result={result(card)} onOpen={onOpen} />)
    fireEvent.click(within(donor('retrieval-chunks')).getByRole('button', { name: /Incident review/ }))
    fireEvent.click(within(screen.getByLabelText('Knowledge citations')).getByRole('button', { name: 'Open source Incident review, Timeline' }))
    expect(onOpen.mock.calls).toEqual([['source-17'], ['source-17']])
  })

  it('uses a recorded line range and summary if section and content are absent', () => {
    render(<KnowledgeRetrievalChunks result={result({ ...card, section: null, content: undefined })} />)
    expect(within(donor('retrieval-chunks')).getByText('Lines 18–24')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).getByText('Recovery was observed.')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).queryByRole('meter')).toBeNull()
  })

  it('states when a passage has no locator, content, summary or relevance score', () => {
    render(<KnowledgeRetrievalChunks result={result({ ...card, section: null, line_range: null, content: undefined, summary: undefined })} />)
    expect(within(donor('retrieval-chunks')).getByText('No passage text available')).toBeInTheDocument()
    expect(within(donor('retrieval-chunks')).queryByRole('meter')).toBeNull()
    expect(within(donor('retrieval-chunks')).queryByRole('button')).toBeNull()
  })

  it('renders honest zero-result donor states without another search control', () => {
    const empty = result()
    const { unmount } = render(<KnowledgeWebSearch result={empty} />)
    expect(within(donor('web-search')).getByText('0 results')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).toBeNull()
    unmount()
    render(<KnowledgeRetrievalChunks result={empty} />)
    expect(within(donor('retrieval-chunks')).getByText('0 passages')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).toBeNull()
  })

  it('keeps one real API request, loading feedback and the existing web/passage split', async () => {
    let complete!: (value: KnowledgeContextResult) => void
    const request = new Promise<KnowledgeContextResult>((resolve) => { complete = resolve })
    const search = vi.spyOn(api, 'knowledgeSearchForContext').mockReturnValue(request)
    const onOpen = vi.fn()
    render(<AuiKnowledgePanel query="  worker recovery  " onOpen={onOpen} />)
    expect(screen.getByRole('status')).toHaveTextContent('Searching knowledge…')
    expect(search).toHaveBeenCalledTimes(1)
    expect(search).toHaveBeenCalledWith('worker recovery')
    await act(async () => complete(result(
      { ...card, id: 'web-source', source_type: 'web_url' },
      { ...card, id: 'note-source', source_type: 'document' },
    )))
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('region', { name: 'Knowledge search results for worker recovery' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Retrieved passages' })).toBeInTheDocument()
    fireEvent.click(within(donor('web-search')).getByRole('button', { name: /Incident review/ }))
    fireEvent.click(within(donor('retrieval-chunks')).getByRole('button', { name: /Incident review/ }))
    expect(onOpen.mock.calls).toEqual([['web-source'], ['note-source']])
    expect(search).toHaveBeenCalledTimes(1)
  })

  it('keeps the panel empty state and real API error without sample records', async () => {
    const search = vi.spyOn(api, 'knowledgeSearchForContext').mockResolvedValueOnce(result())
      .mockRejectedValueOnce(new Error('Search unavailable'))
    const { rerender } = render(<AuiKnowledgePanel query="no matches" />)
    expect(await screen.findByText('No matching knowledge records.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Retrieved passages' })).toBeNull()
    rerender(<AuiKnowledgePanel query="another query" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Search unavailable')
    expect(screen.queryByText('No matching knowledge records.')).toBeNull()
    expect(search).toHaveBeenCalledTimes(2)
  })
})
