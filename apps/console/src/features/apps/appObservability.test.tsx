import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const app = (version: string) => ({
  name: 'notes', displayName: 'Notes', description: 'Take notes', version,
  enabled: true, origin: 'local', source: '/srv/apps/notes', icon: '',
  hasBackend: false, hasUI: false, uiPages: [], isProvider: false,
  providerType: '', hasConfig: false, permissions: {}, tags: [],
  backendRunning: false, backendPort: null, native: false,
})

const catalog = {
  bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [],
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('installed app observability', () => {
  it('re-reads the installed collection after update and repaints the card version', async () => {
    let version = '1.0.0'
    const apps = vi.fn(() => Promise.resolve([app(version)]))
    vi.doMock('../../shared/data/api', async (original) => ({
      ...(await original<Record<string, unknown>>()),
      api: {
        apps,
        appCatalog: () => Promise.resolve(catalog),
        updateApp: () => {
          version = '1.1.0'
          return Promise.resolve({ ok: true, name: 'notes', error: '', needs_consent: false, scan: null })
        },
      },
    }))
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('v1.0.0')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Actions for Notes' }))
    await userEvent.click(screen.getByText('Update…'))
    await userEvent.type(screen.getByPlaceholderText('/path/to/app  or  https://github.com/owner/app.git'), '/srv/apps/notes')
    await userEvent.click(screen.getByRole('button', { name: /^Update$/ }))

    await waitFor(() => expect(screen.getByText('v1.1.0')).toBeTruthy())
    expect(screen.queryByText('v1.0.0')).toBeNull()
    expect(apps).toHaveBeenCalledTimes(2)
  })

  it('does not install an interval-based polling loop', async () => {
    const intervals = vi.spyOn(globalThis, 'setInterval')
    vi.doMock('../../shared/data/api', async (original) => ({
      ...(await original<Record<string, unknown>>()),
      api: {
        apps: () => Promise.resolve([app('1.0.0')]),
        appCatalog: () => Promise.resolve(catalog),
      },
    }))
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('v1.0.0')).toBeTruthy())
    expect(intervals).not.toHaveBeenCalled()
    const source = readFileSync(join(process.cwd(), 'src/features/apps/AppsSection.tsx'), 'utf8')
    expect(source).not.toMatch(/setInterval\s*\(/)
  })
})
