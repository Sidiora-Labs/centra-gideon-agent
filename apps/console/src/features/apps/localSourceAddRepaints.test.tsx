import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'


const PANEL_TITLE = 'Manage Sources'
const NEW_PATH = '/srv/harness-sources/spec-builder'

const CATALOG = {
  bundled: [], gitSources: [], localSources: [],
  localApps: [], remoteApps: [], gitApps: [],
}

function slowCatalog() {
  let calls = 0
  return () => {
    calls += 1
    return calls === 1 ? Promise.resolve(CATALOG) : new Promise(() => {})
  }
}

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([]),
      appCatalog: slowCatalog(),
      addLocalAppSource: (p: string) => Promise.resolve({ ok: true, sources: [p] }),
      ...over,
    },
  }))
}

async function openSourcesPanel() {
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'store' }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy())
  const openers = screen.getAllByRole('button', { name: new RegExp(PANEL_TITLE, 'i') })
  await userEvent.click(openers[0])
  await waitFor(() => expect(screen.getByPlaceholderText(/a directory of app subdirs/)).toBeTruthy())
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the Manage Sources panel re-reads after a successful local-source add', () => {
  it('lists the path it just accepted, without waiting on the catalog re-read', async () => {
    mockApi()
    await openSourcesPanel()
    expect(screen.getByText(/No local sources/), 'the precondition').toBeTruthy()

    const input = screen.getByPlaceholderText(/a directory of app subdirs/)
    await userEvent.type(input, NEW_PATH)
    const addButtons = screen.getAllByRole('button', { name: /^Add$/ })
    await userEvent.click(addButtons[addButtons.length - 1])

    await waitFor(() => expect(screen.getByText(NEW_PATH), 'the added path is listed').toBeTruthy())
    expect(screen.queryByText(/No local sources/), 'and the empty state is gone').toBeNull()
  })

  it('a FAILED add still reports the failure and does not fabricate a row', async () => {
    mockApi({ addLocalAppSource: () => Promise.reject(new Error('not a directory')) })
    await openSourcesPanel()
    const input = screen.getByPlaceholderText(/a directory of app subdirs/)
    await userEvent.type(input, NEW_PATH)
    const addButtons = screen.getAllByRole('button', { name: /^Add$/ })
    await userEvent.click(addButtons[addButtons.length - 1])

    await waitFor(() => expect(screen.getByText(/not a directory/)).toBeTruthy())
    expect(screen.queryByText(NEW_PATH), 'no row for a refused path').toBeNull()
    expect(screen.getByText(/No local sources/), 'still empty, honestly').toBeTruthy()
  })
})
