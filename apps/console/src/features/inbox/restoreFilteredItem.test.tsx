import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../shared/data/api'


const restoreInboxItem = vi.fn((_id: string) => Promise.resolve({}))

vi.mock('../../shared/data/api', () => ({
  api: {
    restoreInboxItem: (id: string) => restoreInboxItem(id),
    updateInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
  },
}))
vi.mock('../../shared/ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../shared/ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../shared/ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function makeItem(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'agent_request_x_100.0',
    channel: '',
    channel_name: '',
    message: 'A flagged claim',
    sender_id: '',
    sender_name: '',
    item_kind: 'agent_request',
    status: 'filtered',
    refs: { verify: 'refuted' },
    ...over,
  } as InboxItem
}

describe('INU-6 Restore in the inbox detail panel', () => {
  beforeEach(() => restoreInboxItem.mockClear())

  it('a filtered item offers Restore and calls the restore endpoint on click', async () => {
    const onChanged = vi.fn()
    render(<InboxDetail item={makeItem()} onChanged={onChanged} navigate={() => {}} />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /restore/i }))
    fireEvent.click(btn)
    await waitFor(() => expect(restoreInboxItem).toHaveBeenCalledWith('agent_request_x_100.0'))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('a delivered (pending) item shows no Restore control', () => {
    render(<InboxDetail item={makeItem({ status: 'pending' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull()
  })
})
