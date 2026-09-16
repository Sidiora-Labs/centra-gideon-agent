import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const okStats = { items: 3, entities: 9, relations: 4, embeddings: { enabled: true, embedded_items: 3, model: 'm' } }
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      knowledgeStats: () => Promise.resolve(okStats),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeIntents: () => Promise.resolve([]),
      ...over,
    },
  }))
}

describe('the knowledge stats strip never states a capability it could not read', () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  it('a failed stats read renders no "semantic search off" claim and no advice', async () => {
    mockApi({ knowledgeStats: boom })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)

    await waitFor(() => expect(screen.getByText('Knowledge')).toBeInTheDocument())

    expect(screen.queryByText(/semantic search off/i), 'a failed read must not claim the capability is off').toBeNull()
    expect(screen.queryByText(/No embedding model active/i), 'and the advice must not appear either').toBeNull()
    expect(screen.queryByText(/Set one in Settings/i)).toBeNull()
  })

  it('a SUCCESSFUL read still renders the strip — or the assertion above proves nothing', async () => {
    mockApi({})
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText('entities')).toBeInTheDocument())
    expect(screen.getByText('relations')).toBeInTheDocument()
  })

  it('a populated library is NOT called empty when the stats read fails', async () => {
    mockApi({ knowledgeStats: boom, knowledgeItems: () => Promise.resolve({ items: [{ id: 'a', title: 'Real note', type: 'note' }] }) })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText('Real note')).toBeInTheDocument())
    expect(screen.queryByText('Knowledge base is empty'), 'a stats outage must not empty a populated library').toBeNull()
  })

  it('and a genuinely DISABLED embedding model still says so — the claim is real then', async () => {
    mockApi({ knowledgeStats: () => Promise.resolve({ ...okStats, embeddings: { enabled: false } }) })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/semantic search off/i)).toBeInTheDocument())
  })
})

describe('the fetcher no longer swallows', () => {
  it('knowledgeStats passes the rejection through', () => {
    const code = readFileSync(join(process.cwd(), "src/features/knowledge/knowledgeStore.ts"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the zeros-and-disabled fallback is the defect').not.toMatch(/knowledgeStats\(\)\.catch/)
    expect(code, 'and the function still exists to be called').toMatch(/export async function knowledgeStats/)
  })
})
