import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { confMeta } from './inboxMeta'
import type { InboxItem } from '../../shared/data/api'


const updateInboxItem = vi.fn((_id: string, _body: Record<string, unknown>) => Promise.resolve({}))

vi.mock('../../shared/data/api', () => ({
  api: {
    updateInboxItem: (id: string, body: Record<string, unknown>) => updateInboxItem(id, body),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
  },
}))
vi.mock('../../shared/ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../shared/ui/FeedbackThumbs', () => ({
  FeedbackThumbs: ({ targetKind }: { targetKind: string }) => <span data-testid={`thumbs-${targetKind}`} />,
}))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../shared/ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function makeItem(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'chan_1.000',
    channel: 'C1',
    channel_name: 'general',
    message: 'hello',
    sender_id: 'U1',
    sender_name: 'Ada',
    item_kind: 'message',
    classification: 'fyi',
    confidence: 'high',
    status: 'seen',
    refs: {},
    ...over,
  } as InboxItem
}

describe('reclassify owns the verdict (issue 623)', () => {
  beforeEach(() => updateInboxItem.mockClear())

  it('reclassifying patches the new classification WITH confidence:user in one call', async () => {
    render(<InboxDetail item={makeItem()} onChanged={() => {}} navigate={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Noise' }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('chan_1.000', {
      classification: 'noise',
      confidence: 'user',
    }))
  })

  it("a user-owned verdict reads 'Set by you' and offers no classification thumbs", () => {
    render(<InboxDetail item={makeItem({ classification: 'noise', confidence: 'user' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Set by you')).toBeInTheDocument()
    expect(screen.queryByText('High confidence')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_classification')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_digest')).not.toBeInTheDocument()
  })

  it('a machine verdict still renders its thumbs (the gate must not over-hide)', () => {
    render(<InboxDetail item={makeItem()} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByTestId('thumbs-inbox_classification')).toBeInTheDocument()
    expect(screen.getByText('High confidence')).toBeInTheDocument()
  })

  it("a digest item's thumbs are gated by the same override", () => {
    render(<InboxDetail item={makeItem({ source: 'digest', confidence: 'user' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByTestId('thumbs-inbox_digest')).not.toBeInTheDocument()
  })

  it("confMeta resolves 'user' to its own entry, not the needs_review fallback", () => {
    expect(confMeta('user').label).toBe('Set by you')
  })
})
