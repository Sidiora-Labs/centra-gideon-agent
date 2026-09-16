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

beforeEach(() => {
  vi.resetModules()
  notified.length = 0
  sessionStorage.clear()
  notifySettled = new Promise((r) => { notifyArrived = r })
})

describe('settings-page copy: a refused retention value rolls back (#624)', () => {
  it('rejected write reverts to the stored value and notifies', async () => {
    mockApi(() => Promise.reject(new Error('retention_days must be an integer >= 1')))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = (await screen.findByLabelText('Retention (days)')) as HTMLInputElement
    fireEvent.change(field, { target: { value: '3000' } })
    fireEvent.blur(field)
    await notifySettled
    expect(notified[0]).toContain("Couldn't save your inbox settings")
    await waitFor(() => expect(field.value).toBe('90'), { timeout: 10_000 })
  }, 20_000)
})
