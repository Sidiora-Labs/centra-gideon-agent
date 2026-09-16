import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { api, type KnowledgeItem } from '../../shared/data/api'
import * as store from './knowledgeStore'


function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k-syn-1',
    title: 'What we know about widgets',
    content: 'A consolidated article about widgets.',
    item_type: 'insight',
    ...over,
  } as KnowledgeItem
}

function staleness(over: Partial<Awaited<ReturnType<typeof api.knowledgeStaleness>>> = {}) {
  return {
    item_id: 'k-syn-1',
    stale: true,
    new_source_items: 3,
    changed_sources: 0,
    checked_at: '2026-08-19T12:00:00Z',
    scope: 'tagged widgets',
    ...over,
  }
}

function stubMount() {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockRejectedValue(new Error('no graph'))
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
}

function renderDetail(it_: KnowledgeItem) {
  return render(
    <KnowledgeDetail item={it_} onChanged={() => {}} onDeleted={() => {}} />,
  )
}

afterEach(() => { vi.restoreAllMocks() })

describe('a synthesis whose sources moved on', () => {
  it('names the COUNT of new source items and offers exactly one action', async () => {
    stubMount()
    const ask = vi.spyOn(api, 'knowledgeStaleness').mockResolvedValue(staleness() as never)
    renderDetail(item())

    const banner = await waitFor(() => screen.getByRole('status'))
    expect(ask).toHaveBeenCalledWith('k-syn-1')
    expect(banner.textContent).toMatch(/3 new source items/)
    expect(banner.textContent).toMatch(/tagged widgets/)
    const actions = banner.querySelectorAll('button')
    expect(actions.length, 'the banner offers ONE action, not a menu').toBe(1)
    expect(actions[0].textContent).toMatch(/Regenerate/)
  })

  it('pluralises a single item and reports edited sources separately', async () => {
    stubMount()
    vi.spyOn(api, 'knowledgeStaleness')
      .mockResolvedValue(staleness({ new_source_items: 1, changed_sources: 2 }) as never)
    renderDetail(item())

    const banner = await waitFor(() => screen.getByRole('status'))
    expect(banner.textContent).toMatch(/1 new source item[^s]/)
    expect(banner.textContent).toMatch(/2 cited sources edited/)
  })

  it('regenerating queues a proposal rather than rewriting in place', async () => {
    stubMount()
    vi.spyOn(api, 'knowledgeStaleness').mockResolvedValue(staleness() as never)
    const regen = vi.spyOn(api, 'knowledgeRegenerate').mockResolvedValue({
      ok: true, item_id: 'k-syn-1', already_pending: false,
      proposal: { proposal_id: 'p-1', applied: false, pending: true, reason: 'awaiting review' },
    } as never)
    renderDetail(item())

    const button = await waitFor(() => screen.getByRole('button', { name: /Regenerate this synthesis/i }))
    button.click()
    await waitFor(() => expect(regen).toHaveBeenCalledWith('k-syn-1'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Queued/))
  })

  it('says so when a proposal was ALREADY queued, instead of implying a second one', async () => {
    stubMount()
    vi.spyOn(api, 'knowledgeStaleness').mockResolvedValue(staleness() as never)
    vi.spyOn(api, 'knowledgeRegenerate').mockResolvedValue({
      ok: true, item_id: 'k-syn-1', already_pending: true,
      proposal: { proposal_id: 'p-1', applied: false, pending: true, reason: 'awaiting review' },
    } as never)
    renderDetail(item())

    const button = await waitFor(() => screen.getByRole('button', { name: /Regenerate this synthesis/i }))
    button.click()
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Already queued/))
  })
})

describe('the banner stays silent when it has nothing to say', () => {
  it('does not ask about a non-synthesized item at all', async () => {
    stubMount()
    const ask = vi.spyOn(api, 'knowledgeStaleness').mockResolvedValue(staleness() as never)
    renderDetail(item({ item_type: 'note' }))

    await waitFor(() => expect(store.getKnowledge).toHaveBeenCalledWith('k-syn-1'))
    expect(ask, 'an observed item cannot be stale — asking would be a wasted request').not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('renders nothing when the server says the synthesis is current', async () => {
    stubMount()
    vi.spyOn(api, 'knowledgeStaleness')
      .mockResolvedValue(staleness({ stale: false, new_source_items: 0 }) as never)
    renderDetail(item())

    await waitFor(() => expect(api.knowledgeStaleness).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a failed check does not displace the document', async () => {
    stubMount()
    vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('offline'))
    renderDetail(item())

    await waitFor(() => expect(api.knowledgeStaleness).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
  })
})
