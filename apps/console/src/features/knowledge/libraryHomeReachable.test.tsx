import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { KnowledgeListPage } from './KnowledgeListPage'
import { api } from '../../shared/data/api'
import { resetDataStore } from '../../shared/data/data/store'


function mount(view = '') {
  const query: Record<string, string> = view ? { view } : {}
  const setQuery = vi.fn()
  render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenReader={() => {}}
    onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} query={query} setQuery={setQuery} />)
  return setQuery
}

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 3, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [], total: 0, page: 1, limit: 100 } as never)
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue({
    recently_added: [], continue_reading: [], favorites: [],
    collections: [{ id: 'c1', name: 'Recipes', kind: 'manual', count: 2 }],
  })
})

describe('the Home lens is on the page, not just in the file tree', () => {
  it('offers Home FIRST in the view strip', async () => {
    mount()
    expect(await screen.findByRole('tab', { name: /Home/ })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent))
      .toEqual(['Home', 'Library', 'Graph', 'Intents', 'Tags', 'Conflicts', 'Decisions'])
  })

  it('renders the shelves when Home is the active lens', async () => {
    mount('home')
    expect(await screen.findByRole('region', { name: 'Shelves' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Continue reading' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Recently added' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Favorites' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Recipes, 2 items' })).toBeInTheDocument()
  })

  it('does NOT render them under a lens that is not Home', async () => {
    mount('graph')
    await screen.findByRole('tab', { name: /Home/ })
    expect(screen.queryByRole('region', { name: 'Recently added' })).toBeNull()
  })

  it('selecting Home puts the lens in the URL, so it is linkable and survives a reload', async () => {
    const setQuery = mount()
    await userEvent.click(await screen.findByRole('tab', { name: /Home/ }))
    expect(setQuery).toHaveBeenCalledWith({ view: 'home' }, expect.anything())
  })
})
