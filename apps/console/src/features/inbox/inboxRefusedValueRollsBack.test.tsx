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
