import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'


const notified: string[] = []
let notifyArrived!: () => void
let notifySettled: Promise<void>

function mockApi(saveInboxSettings: () => Promise<unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        inboxSettings: () =>
          Promise.resolve({ auto_cleanup_enabled: true, retention_days: 90 }),
        gideonConfig: () =>
          Promise.resolve({ inbox: { engagement_ranking_enabled: false, enabled: false }, proactive: {} }),
        proactiveStatus: () => Promise.resolve({}),
        saveInboxSettings,
      },
    }
  })
  vi.doMock('../../app/shell/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg); notifyArrived() },
  }))
}

async function driveRetentionTo(value: string) {
  const field = await screen.findByLabelText('Retention (days)')
  fireEvent.change(field, { target: { value } })
  fireEvent.blur(field)
  return field as HTMLInputElement
}

beforeEach(() => {
  vi.resetModules()
  notified.length = 0
  sessionStorage.clear()
  notifySettled = new Promise((r) => { notifyArrived = r })
})

describe('a refused retention value rolls back and says so (#624)', () => {
  it('drawer copy (pages/inbox): rejected write reverts to the stored value', async () => {
    mockApi(() => Promise.reject(new Error('retention_days must be an integer >= 1')))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = await driveRetentionTo('3000')
    await notifySettled
    expect(notified[0]).toContain("Couldn't save your inbox settings")
    await waitFor(() => expect(field.value).toBe('90'), { timeout: 10_000 })
  }, 20_000)

  it('drawer copy: an accepted write keeps the new value (rollback is not a revert-always)', async () => {
    mockApi(() => Promise.resolve({}))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = await driveRetentionTo('120')
    await waitFor(() => expect(field.value).toBe('120'), { timeout: 4000 })
    expect(notified).toEqual([])
  })
})

describe('watched-channel editor', () => {
  it('writes the read revision, removes channels, and re-applies a stale add without losing another channel', async () => {
    vi.doUnmock('../../shared/data/api')
    const { api } = await import('../../shared/data/api')
    let stored = ['C_KEEP']
    let revision = 'r1'
    const writes: Array<{ path: string; value: unknown; basedOn?: string }> = []
    vi.spyOn(api, 'gideonConfig').mockImplementation(async () => ({ inbox: { watched_channels: [...stored] }, revisions: { 'inbox.watched_channels': revision } }))
    vi.spyOn(api, 'inboxProviders').mockResolvedValue([{ name: 'slack', display_name: 'Slack', source_name: 'slack', watches_channels: true }])
    vi.spyOn(api, 'patchConfig').mockImplementation(async (path, value, basedOn) => {
      writes.push({ path, value, basedOn })
      if (basedOn !== revision) throw Object.assign(new Error('The channel settings changed.'), { code: 'stale_write' })
      stored = [...value as string[]]
      revision = `r${Number(revision.slice(1)) + 1}`
      return {}
    })
    try {
      const { WatchedChannelsField } = await import('./WatchedChannelsField')
      render(<WatchedChannelsField />)
      const field = await screen.findByRole('textbox', { name: 'Channel ID' })
      fireEvent.change(field, { target: { value: 'C_ADD' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add channel' }))
      await screen.findByText('C_ADD')
      expect(writes[0]).toEqual({ path: 'inbox.watched_channels', value: ['C_KEEP', 'C_ADD'], basedOn: 'r1' })
      fireEvent.click(screen.getByRole('button', { name: 'Remove watched channel 2 of 2' }))
      await waitFor(() => expect(screen.queryByText('C_ADD')).toBeNull())
      expect(writes[1]).toEqual({ path: 'inbox.watched_channels', value: ['C_KEEP'], basedOn: 'r2' })
      stored = ['C_KEEP', 'C_ELSEWHERE']
      revision = 'r4'
      fireEvent.change(field, { target: { value: 'C_MINE' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add channel' }))
      await screen.findByText(/Your watched channels changed elsewhere/)
      expect(stored).toEqual(['C_KEEP', 'C_ELSEWHERE'])
      expect(writes[2]).toEqual({ path: 'inbox.watched_channels', value: ['C_KEEP', 'C_MINE'], basedOn: 'r3' })
      expect((field as HTMLInputElement).value).toBe('C_MINE')
      const reapply = screen.getByRole('button', { name: 'Reload and reapply' })
      await waitFor(() => expect(reapply).not.toHaveAttribute('aria-busy', 'true'))
      fireEvent.click(reapply)
      await screen.findByText('C_MINE')
      expect(stored).toEqual(['C_KEEP', 'C_ELSEWHERE', 'C_MINE'])
      expect(writes[3]).toEqual({ path: 'inbox.watched_channels', value: ['C_KEEP', 'C_ELSEWHERE', 'C_MINE'], basedOn: 'r4' })
      expect(screen.queryByText(/Your watched channels changed elsewhere/)).toBeNull()
    } finally { vi.restoreAllMocks() }
  })

  it('refuses to write without a read revision and does not admit providers that do not watch channels', async () => {
    vi.doUnmock('../../shared/data/api')
    const { api } = await import('../../shared/data/api')
    vi.spyOn(api, 'gideonConfig').mockResolvedValue({ inbox: { watched_channels: ['C_KEEP'] } })
    vi.spyOn(api, 'inboxProviders').mockResolvedValue([{ name: 'slack', display_name: 'Slack', source_name: 'slack', watches_channels: true }])
    const write = vi.spyOn(api, 'patchConfig')
    try {
      const { WatchedChannelsField } = await import('./WatchedChannelsField')
      const view = render(<WatchedChannelsField />)
      expect(await screen.findByRole('alert')).toHaveTextContent('Inbox channel settings have not been read with a revision.')
      expect(screen.queryByRole('textbox', { name: 'Channel ID' })).toBeNull()
      expect(write).not.toHaveBeenCalled()
      view.unmount()
      vi.spyOn(api, 'gideonConfig').mockResolvedValue({ inbox: { watched_channels: ['C_KEEP'] }, revisions: { 'inbox.watched_channels': 'r1' } })
      vi.spyOn(api, 'inboxProviders').mockResolvedValue([{ name: 'filesystem', display_name: 'Filesystem', source_name: 'filesystem', watches_channels: false }])
      render(<WatchedChannelsField />)
      await waitFor(() => expect(api.inboxProviders).toHaveBeenCalledTimes(2))
      expect(screen.queryByRole('textbox', { name: 'Channel ID' })).toBeNull()
      expect(write).not.toHaveBeenCalled()
    } finally { vi.restoreAllMocks() }
  })
})
