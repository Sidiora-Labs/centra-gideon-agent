import { readFileSync } from 'node:fs'
import { describe, expect, it, vi, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { InboxQueueRow, InboxSortHold } from './InboxPage'
import { InboxSettingsPanel } from '../settings/InboxSettingsPanel'
import { acceptsInboxFilter } from './inboxQueueState'
import { api, type InboxItem, type InboxStatus } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'

// Produced by the real InboxStore/SDK/local HTTP/API runtime check. This is an
// API contract consumer gate, not a live browser or live model certification.
const fixturePath = process.env.INBOX_CONSUMER_FIXTURE
if (!fixturePath) throw new Error('Run the real API producer gate with INBOX_CONSUMER_FIXTURE first')
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8')) as {
  unsorted: InboxItem; failed: InboxItem; sorted: InboxItem; retried: InboxItem; status: InboxStatus
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function detail(item: InboxItem) {
  return render(<InboxDetail item={item} onChanged={() => {}} navigate={() => {}} />)
}

describe('native Inbox consumes actual API sorting/provenance', () => {
  it('lists an unsorted message honestly and keeps unrelated attention kinds out of reply counts', () => {
    render(<InboxQueueRow item={fixture.unsorted} index={0} onOpen={() => {}} navigate={() => {}} owner="" />)
    expect(screen.getByText('Not sorted yet')).toBeTruthy()
    expect(screen.queryByTitle('Needs review')).toBeNull()
    expect(screen.queryByText('Needs reply')).toBeNull()
    expect(acceptsInboxFilter({ ...fixture.sorted, classification: 'needs_reply', item_kind: 'system' }, 'needs_reply')).toBe(false)
    expect(acceptsInboxFilter({ ...fixture.sorted, classification: 'needs_reply', item_kind: 'email' }, 'needs_reply')).toBe(true)
  })
  it('shows a real failure, sends Sort again to its endpoint and preserves the draft', async () => {
    const retry = vi.spyOn(api, 'sortInboxItem').mockResolvedValue(fixture.retried)
    detail(fixture.failed)
    expect(screen.getByText("Couldn't sort")).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Mark accurate' })).toBeNull()
    expect(screen.queryByText('Needs review')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Sort again' }))
    await waitFor(() => expect(retry).toHaveBeenCalledWith(fixture.failed.id))
    expect((screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement).value).toBe(fixture.failed.draft)
  })
  it('exposes feedback only for the stored maker and reports the actual hold', async () => {
    vi.spyOn(api, 'feedbackTarget').mockResolvedValue({ verdict: null } as Awaited<ReturnType<typeof api.feedbackTarget>>)
    const write = vi.spyOn(api, 'recordFeedback').mockResolvedValue({ ok: true } as Awaited<ReturnType<typeof api.recordFeedback>>)
    detail(fixture.sorted)
    fireEvent.click(await screen.findByRole('button', { name: 'Mark accurate' }))
    await waitFor(() => expect(write).toHaveBeenCalled())
    expect(write.mock.calls[0][0].producer_id).toBe(fixture.sorted.classified_by)
    cleanup()
    render(<InboxSortHold held={fixture.status.health.sorting!.held} waiting={fixture.status.health.sorting!.waiting} />)
    expect(screen.getByRole('status').textContent).toContain(fixture.status.health.sorting!.held)
    expect(screen.getByRole('status').textContent).toContain('message')
  })
  it('saves the native sorting switch and rolls back a refused setting', async () => {
    for (const key of ['settings:inbox', 'settings:inbox-config', 'settings:approval-rules']) invalidateKeys(key)
    vi.spyOn(api, 'inboxSettings').mockResolvedValue({ auto_cleanup_enabled: true, retention_days: 90 })
    vi.spyOn(api, 'gideonConfig').mockResolvedValue({ inbox: { enabled: true, sort_messages: true, engagement_ranking_enabled: false }, proactive: {} })
    vi.spyOn(api, 'approvalRules').mockResolvedValue({ rules: [], unreadable: [] })
    const patch = vi.spyOn(api, 'patchConfig').mockRejectedValue(new Error('Setting refused'))
    render(<InboxSettingsPanel />)
    const toggle = await screen.findByRole('switch', { name: 'Sort new messages' })
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
    fireEvent.click(toggle)
    await waitFor(() => expect(patch).toHaveBeenCalledWith('inbox.sort_messages', false))
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
  })
})
