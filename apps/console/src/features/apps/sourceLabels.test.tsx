import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { SourcesPanel } from './AppsSection'


const removeAppSource = vi.fn((_url: string) => Promise.resolve({}))

vi.mock('../../shared/data/api', () => ({
  api: {
    removeAppSource: (url: string) => removeAppSource(url),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
    installApp: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../shared/data/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve({ ok: true }),
    confirmInstall: () => Promise.resolve({ ok: true }),
    reset: () => {},
    blocked: null,
    busy: false,
    error: null,
    fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => null,
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

const BUNDLED = 'https://apps.example.test/bundled.git'
const REGISTRY = 'https://apps.example.test/registry.git'
const USER_ADDED = 'https://github.com/acme/cool-app.git'

const catalog = {
  bundled: [],
  gitSources: [BUNDLED, REGISTRY, USER_ADDED],
  defaultGitSources: [BUNDLED, REGISTRY],
  builtinGitSources: [BUNDLED],
  localSources: [],
  firstPartySources: [],
  localApps: [],
  remoteApps: [],
  gitApps: [],
}

function rowFor(url: string): HTMLElement {
  const cell = screen.getByText(url)
  const row = cell.parentElement
  if (!row) throw new Error(`no row for ${url}`)
  return row
}

describe('the Store source list labels shipped defaults', () => {
  beforeEach(() => removeAppSource.mockClear())

  it('labels both kinds of shipped default and nothing else', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(REGISTRY)).toBeTruthy())
    expect(rowFor(BUNDLED).textContent).toContain('Default')
    expect(rowFor(REGISTRY).textContent).toContain('Default')
    expect(rowFor(USER_ADDED).textContent).not.toContain('Default')
  })

  it('keeps the seeded default removable and removes it by its own URL', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = await waitFor(() => rowFor(REGISTRY))
    const remove = row.querySelector('[aria-label="Remove source"]') as HTMLElement | null
    expect(remove).toBeTruthy()
    fireEvent.click(remove!)
    await waitFor(() => expect(removeAppSource).toHaveBeenCalledWith(REGISTRY))
  })

  it('offers no remove control on the bundled default, whose DELETE is a no-op', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(BUNDLED)).toBeTruthy())
    expect(rowFor(BUNDLED).querySelector('[aria-label="Remove source"]')).toBeNull()
    expect(rowFor(USER_ADDED).querySelector('[aria-label="Remove source"]')).toBeTruthy()
  })

  it('treats a backend with no labels as "nothing is a default"', async () => {
    render(<SourcesPanel catalog={{ ...catalog, defaultGitSources: undefined, builtinGitSources: undefined }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(REGISTRY)).toBeTruthy())
    expect(rowFor(REGISTRY).textContent).not.toContain('Default')
    expect(rowFor(BUNDLED).querySelector('[aria-label="Remove source"]')).toBeTruthy()
  })
})
