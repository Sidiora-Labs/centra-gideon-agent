import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, cleanup } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { api, type InboxItem, type InboxDraftResult } from '../../shared/data/api'

const item = { id: 'item', channel: 'team', channel_name: 'Team', thread_ts: null,
  message: 'Can we meet Friday?', sender_id: 'opaque-user-19', sender_name: 'Morgan',
  classification: 'needs_reply', confidence: 'user', status: 'pending', source: 'native',
  draft: 'My existing reply', can_reply: true, created_at: 0, item_kind: 'message' } as InboxItem
const report = { question: '', warnings: [], named_notes: [], word_limit: null, words: 2, wrote: true, skipped: false }

describe('native inbox draft review', () => {
  it('sends owner words separately and retains edits made while a draft is generated', async () => {
    let resolve!: (value: InboxDraftResult) => void
    const call = vi.spyOn(api, 'draftInboxReply').mockImplementation(() => new Promise(done => { resolve = done }))
    render(<InboxDetail item={item} onChanged={() => {}} navigate={() => {}} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your reply instructions' }), { target: { value: 'Ask which topic' } })
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }))
    expect(call).toHaveBeenCalledWith('item', 'Ask which topic')
    fireEvent.change(screen.getByRole('textbox', { name: 'Drafted reply' }), { target: { value: 'My new edit' } })
    await act(async () => { resolve({ ...item, draft: 'Generated reply', drafting: report }) })
    expect((screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement).value).toBe('My new edit')
    expect(screen.getByText('Your edits made during generation are retained in the reply editor.')).toBeTruthy()
    call.mockRestore(); cleanup()
  })
  it('shows the clarification and unavailable named evidence without overwriting the editor', async () => {
    const call = vi.spyOn(api, 'draftInboxReply').mockResolvedValue({ ...item, drafting: {
      ...report, wrote: false, question: 'Which topic should the reply cover?',
      named_notes: [{ name: 'plan.md', available: false, reason: 'Scoped named-note reading is unavailable.' }],
    } })
    render(<InboxDetail item={{ ...item, sender_name: '' }} onChanged={() => {}} navigate={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }))
    await screen.findByText('Before drafting: Which topic should the reply cover?')
    expect(screen.getByText('plan.md: Scoped named-note reading is unavailable.')).toBeTruthy()
    expect(screen.getByText('Unknown sender')).toBeTruthy()
    expect(screen.queryByText('opaque-user-19')).toBeNull()
    expect((screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement).value).toBe('My existing reply')
    call.mockRestore(); cleanup()
  })
})
