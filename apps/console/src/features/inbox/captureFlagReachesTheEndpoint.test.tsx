import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxPage } from './InboxPage'
import type { InboxItem, InboxStatus } from '../../shared/data/api'


const createInboxNote = vi.fn((_text: string) =>
  Promise.resolve({ ok: true, id: 'user_note_abc_1.0', item: {} as InboxItem }),
)

const ITEMS: InboxItem[] = []
const STATUS: InboxStatus = {
  enabled: true,
  pending_count: 0,
  total_count: 0,
  health: { running: true },
  sources: [],
  watched_channels: [],
}

vi.mock('../../shared/data/api', () => ({
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve(STATUS),
    createInboxNote: (text: string) => createInboxNote(text),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    openInboxItem: () => Promise.resolve({ ok: true }),
    updateInboxItem: () => Promise.resolve({} as InboxItem),
    favoriteInboxItem: () => Promise.resolve({ ok: true, favorited: true }),
    draftInboxReply: () => Promise.resolve({} as InboxItem),
    sendInboxReply: () => Promise.resolve({ ok: true }),
    restoreInboxItem: () => Promise.resolve({} as InboxItem),
    dismissAllInbox: () => Promise.resolve({ ok: true, dismissed: 0 }),
    restartInbox: () => Promise.resolve({ ok: true }),
    digestInboxChannel: () => Promise.resolve({} as InboxItem),
  },
}))
vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

function renderInbox(query: Record<string, string>) {
  const setQuery = vi.fn()
  const r = render(<InboxPage query={query} setQuery={setQuery} navigate={() => {}} />)
  return { ...r, setQuery }
}

function composer(): HTMLElement | null {
  return screen.queryByRole('dialog', { name: /capture a note/i })
}

describe('INU-9 — the tray deep link reaches the note endpoint', () => {
  beforeEach(() => createInboxNote.mockClear())

  it('`?capture=1` opens the compose surface', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
  })

  it('and saving from it POSTs the note text', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    const box = screen.getByRole('textbox', { name: /note/i })
    fireEvent.change(box, { target: { value: '  Chase the invoice discrepancy  ' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))
    await waitFor(() =>
      expect(createInboxNote).toHaveBeenCalledWith('Chase the invoice discrepancy'),
    )
  })

  it('closing the composer clears the flag, so Back does not reopen it', async () => {
    const { setQuery } = renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    await waitFor(() => expect(setQuery).toHaveBeenCalledWith({ capture: null }, undefined))
  })

  it('plain inbox navigation renders the inbox and NO compose surface', async () => {
    renderInbox({})
    await waitFor(() => expect(screen.getByText('Inbox')).toBeTruthy())
    expect(composer(), 'an unrequested modal would hijack every visit to the inbox').toBeNull()
    expect(createInboxNote).not.toHaveBeenCalled()
  })

  it('the header offers the same capture surface, so the tray is not its only entrance', async () => {
    const { setQuery } = renderInbox({})
    const btn = await waitFor(() => screen.getByRole('button', { name: /capture a note/i }))
    fireEvent.click(btn)
    expect(setQuery).toHaveBeenCalledWith({ capture: '1' }, undefined)
  })
})

describe('a non-channel row names itself by its KIND, header included', () => {
  const row = (over: Partial<InboxItem>): InboxItem => ({
    id: 'x_1.0', channel: '', channel_name: '', message: 'the body',
    sender_id: 'user', sender_name: 'user', classification: 'needs_reply',
    confidence: 'high', status: 'seen', ...over,
  } as InboxItem)

  it.each([
    ['user_note', 'Notes'],
    ['digest', 'Digests'],
    ['needs_input', 'Needs you'],
  ])('a %s panel is titled by its kind (%s), not by the emitting subsystem', async (kind, label) => {
    ITEMS.splice(0, ITEMS.length, row({ id: 'k_1.0', item_kind: kind as InboxItem['item_kind'] }))
    try {
      renderInbox({ open: 'k_1.0' })
      await waitFor(() => expect(screen.getByRole('region', { name: label })).toBeTruthy())
      expect(screen.queryByRole('region', { name: 'user' })).toBeNull()
    } finally {
      ITEMS.length = 0
    }
  })

  it('a channel-backed row still leads with its sender — that IS its identity', async () => {
    ITEMS.splice(0, ITEMS.length, row({
      id: 'm_1.0', item_kind: 'message', sender_name: 'Priya', channel_name: 'ops',
    }))
    try {
      renderInbox({ open: 'm_1.0' })
      await waitFor(() => expect(screen.getByRole('region', { name: 'Priya' })).toBeTruthy())
    } finally {
      ITEMS.length = 0
    }
  })
})

describe('INU-9 — a capture that fails must not eat the note', () => {
  beforeEach(() => createInboxNote.mockClear())

  it("shows the server's own sentence and KEEPS the typed text", async () => {
    createInboxNote.mockImplementationOnce(() =>
      Promise.reject(new Error('The note could not be written to the inbox, so it was not kept.')),
    )
    renderInbox({ capture: '1' })
    const box = await waitFor(() => screen.getByRole('textbox', { name: /note/i }))
    fireEvent.change(box, { target: { value: 'Something I cannot afford to lose' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))

    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toMatch(/could not be written/i)
    expect((box as HTMLTextAreaElement).value).toBe('Something I cannot afford to lose')
    expect(composer()).not.toBeNull()
  })

  it('an empty note never reaches the network', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    const box = screen.getByRole('textbox', { name: /note/i })
    fireEvent.change(box, { target: { value: '   \n  ' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))
    expect(createInboxNote).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /save to inbox/i })).toHaveAttribute('title', 'Write the note first')
  })
})
