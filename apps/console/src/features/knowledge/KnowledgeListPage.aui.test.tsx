import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KnowledgeListPage } from './KnowledgeListPage'
import { api, type KnowledgeContextResult, type KnowledgeItem, type KnowledgeStats } from '../../shared/data/api'
import { resetDataStore } from '../../shared/data/data/store'

const item: KnowledgeItem = { id: 'note-17', title: 'Indexing notes', type: 'note', content: 'A stored note about indexing.' }
const stats: KnowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
const context: KnowledgeContextResult = {
  query: 'indexing notes',
  results: [
    { id: item.id, title: item.title!, source_type: 'note', tokens: 12, content: item.content },
    { id: 'bookmark-9', title: 'Indexing reference', source_type: 'bookmark', tokens: 8,
      summary: 'A saved reference on indexing.', deep_link: 'https://docs.example.org/indexing' },
  ],
  total_tokens: 20,
  max_tokens: 4000,
}

function mount(query: Record<string, string> = {}, onOpenItem = vi.fn()) {
  const setQuery = vi.fn()
  render(<KnowledgeListPage query={query} setQuery={setQuery} onCreate={vi.fn()}
    onOpenItem={onOpenItem} onOpenSources={vi.fn()} onOpenReports={vi.fn()} onOpenChat={vi.fn()} />)
  return { onOpenItem, setQuery }
}

function RoutedPage({ initialQuery = {} }: { initialQuery?: Record<string, string> }) {
  const [query, setRoute] = useState(initialQuery)
  const setQuery = (patch: Record<string, string | null | undefined>) => setRoute((current) => {
    const next = { ...current }
    for (const [key, value] of Object.entries(patch)) {
      if (value == null) delete next[key]
      else next[key] = value
    }
    return next
  })
  return <KnowledgeListPage query={query} setQuery={setQuery} onCreate={vi.fn()}
    onOpenItem={vi.fn()} onOpenSources={vi.fn()} onOpenReports={vi.fn()} onOpenChat={vi.fn()} />
}

beforeEach(() => {
  vi.restoreAllMocks()
  resetDataStore()
  localStorage.clear()
  sessionStorage.clear()
  vi.spyOn(api, 'knowledgeStats').mockResolvedValue(stats)
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [item], total: 1, page: 1, limit: 100 })
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'knowledgeSearchForContext').mockResolvedValue(context)
})

describe('connected Knowledge search in the existing library', () => {
  it('keeps the list controls and does not request context for an empty route query', async () => {
    mount()
    expect(await screen.findByRole('searchbox', { name: 'Search knowledge' })).toBeInTheDocument()
    expect((await screen.findAllByText('Indexing notes')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Show archived/ })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Knowledge search' })).toBeNull()
    expect(api.knowledgeSearchForContext).not.toHaveBeenCalled()
    expect(api.knowledgeItems).toHaveBeenCalledWith(expect.objectContaining({ q: undefined }))
  })

  it('does not request context for whitespace-only submitted search', async () => {
    mount({ q: '   ' })
    await screen.findByRole('searchbox', { name: 'Search knowledge' })
    await screen.findByText('Indexing notes')
    expect(screen.queryByRole('region', { name: 'Knowledge search' })).toBeNull()
    expect(api.knowledgeSearchForContext).not.toHaveBeenCalled()
  })

  it('does not request context while another Knowledge view owns the route', async () => {
    vi.mocked(api.knowledgeStats).mockResolvedValue({ ...stats, items: 0 })
    mount({ view: 'graph', q: 'indexing notes' })
    await screen.findByText('Knowledge base is empty')
    expect(screen.queryByRole('region', { name: 'Knowledge search' })).toBeNull()
    expect(api.knowledgeSearchForContext).not.toHaveBeenCalled()
  })

  it('queries the typed context API only after a submitted search and leaves the item list visible', async () => {
    mount({ q: '  indexing notes  ' })
    expect(await screen.findByRole('region', { name: 'Knowledge search' })).toBeInTheDocument()
    await waitFor(() => expect(api.knowledgeSearchForContext).toHaveBeenCalledWith('indexing notes'))
    expect(api.knowledgeSearchForContext).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('searchbox', { name: 'Search knowledge' })).toHaveValue('  indexing notes  ')
    expect((await screen.findAllByText('Indexing notes')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Show archived/ })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Knowledge search' })).toBeInTheDocument()
  })

  it('renders typed web and passage results and routes each source through onOpenItem', async () => {
    const onOpenItem = vi.fn()
    mount({ q: context.query }, onOpenItem)
    const web = await screen.findByRole('region', { name: `Knowledge search results for ${context.query}` })
    expect(web).toHaveAttribute('data-slot', 'web-search')
    const passages = screen.getByRole('region', { name: 'Retrieved passages' })
    expect(passages).toHaveAttribute('data-slot', 'retrieval-chunks')
    fireEvent.click(screen.getByRole('button', { name: 'Open source Indexing reference' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open source Indexing notes' }))
    expect(onOpenItem.mock.calls).toEqual([['bookmark-9'], [item.id]])
  })

  it('shows the real context API error while preserving the existing list', async () => {
    vi.mocked(api.knowledgeSearchForContext).mockRejectedValue(new Error('Knowledge search unavailable'))
    mount({ q: 'indexing notes' })
    const panel = await screen.findByRole('region', { name: 'Knowledge search' })
    expect(await screen.findByRole('alert')).toHaveTextContent('Knowledge search unavailable')
    expect(panel).toBeInTheDocument()
    expect(await screen.findByText('Indexing notes')).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search knowledge' })).toBeInTheDocument()
  })

  it('reports an empty context response without adding a sample result', async () => {
    vi.mocked(api.knowledgeSearchForContext).mockResolvedValue({
      query: 'unmatched phrase', results: [], total_tokens: 0, max_tokens: 4000,
    })
    mount({ q: 'unmatched phrase' })
    expect(await screen.findByText('No matching knowledge records.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Retrieved passages' })).toBeNull()
    expect(screen.queryByRole('region', { name: /Knowledge search results for/ })).toBeNull()
  })

  it('uses the existing debounced search field to submit a query', async () => {
    render(<RoutedPage />)
    const search = await screen.findByRole('searchbox', { name: 'Search knowledge' })
    fireEvent.change(search, { target: { value: context.query } })
    expect(api.knowledgeSearchForContext).not.toHaveBeenCalled()
    await waitFor(() => expect(api.knowledgeSearchForContext).toHaveBeenCalledWith(context.query), { timeout: 2000 })
    expect(await screen.findByRole('region', { name: 'Knowledge search' })).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search knowledge' })).toHaveValue(context.query)
  })
})
