import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AppCatalogEntry } from '../../shared/data/api'


const blocked = {
  ok: false,
  needsConsent: true,
  scan: { verdict: 'warning', findings: [{ surface: 'script', severity: 'warning', rule: 'python_exec', path: 'server/provider.py', evidence: 'subprocess.run(["curl", "-s", url])' }], signature: null },
}

vi.mock('../../shared/data/api', () => ({
  api: {
    installApp: () => Promise.resolve(blocked),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../shared/data/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve(blocked),
    confirmInstall: () => Promise.resolve(blocked),
    reset: () => {},
    blocked,
    busy: false,
    error: null,
    fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => true,
  terminalRefusalReason: () => '',
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView, SourcesPanel } from './AppsSection'

const SOURCE = 'https://github.com/acme/reporter.git'

const entry: AppCatalogEntry = {
  name: 'reporter', displayName: 'Reporter', description: 'reports', version: '1.0.0',
  icon: '', author: 'acme', source: SOURCE, sourceKind: 'git',
  isProvider: false, providerType: '', tags: [],
  permissions: { api: ['/api/knowledge'], cron: true, agent: true, network: false },
  crons: [{ name: 'nightly-digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour', agent: 'researcher', message: 'summarise the day' }],
}

const catalog = {
  bundled: [], gitSources: [SOURCE], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

const dialogText = () =>
  (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')

async function openConsentFrom(button: HTMLElement) {
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
}

describe('a card-grid install discloses the grants at consent', () => {
  const grid = () => render(
    <StoreView catalog={catalog} result={[{ ...entry, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )

  it('shows the enforced permissions and the scheduled job, not just the scan', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    expect(text).toMatch(/Security scan: warning/)
    expect(text).toMatch(/python_exec/)
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/API: \/api\/knowledge/)
    expect(text).toMatch(/Run background agents/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('carries the app’s own network claim through, rather than a default', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    expect(dialogText()).toMatch(/Network access: declared as denied/)
  })

  it('words the cron cadence instead of showing the crontab line', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    expect(text).toMatch(/At 23 minutes past the hour/)
    expect(text, 'the raw expression belongs in the tooltip, not the row').not.toMatch(/23 \* \* \* \*/)
    expect(screen.getByRole('dialog').querySelector('[title="cron: 23 * * * *"]')).toBeTruthy()
  })

  it('glosses the scanner rule in words a non-expert can act on', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    expect(text).toMatch(/This code runs an external program on your machine\./)
    expect(text, 'the map must not name one of its two surfaces').not.toMatch(/The app runs an external/)
    expect(text).toMatch(/subprocess\.run/)
  })
})

describe('a Manage Sources install discloses the grants for an indexed source', () => {
  it('resolves the catalog entry for the source URL and shows its grants', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(SOURCE).parentElement!
    await openConsentFrom(row.querySelector('button')!)
    const text = dialogText()
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('states that the grants are unknown for a source the catalog has not indexed', async () => {
    const unindexed = 'https://github.com/acme/unknown.git'
    render(<SourcesPanel catalog={{ ...catalog, gitSources: [unindexed], gitApps: [] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(unindexed).parentElement!
    await openConsentFrom(row.querySelector('button')!)
    const text = dialogText()
    expect(text).toMatch(/could not read this app's declared permissions/)
    expect(text).not.toMatch(/granted no gateway capability/)
  })
})
