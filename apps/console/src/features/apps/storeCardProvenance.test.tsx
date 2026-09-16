import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalogEntry, AppCatalog } from '../../shared/data/api'


vi.mock('../../shared/data/api', () => ({
  api: {
    installApp: () => Promise.resolve({ ok: true }),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../shared/data/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve({ ok: true }),
    confirmInstall: () => Promise.resolve({ ok: true }),
    reset: () => {}, blocked: null, busy: false, error: null, fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => '',
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView, SourcesPanel } from './AppsSection'

const LOCAL_DESC = 'Ask one question and walk away…'
const REMOTE_DESC = 'Unattended research campaigns as an agent tool…'

function entry(over: Partial<AppCatalogEntry> = {}): AppCatalogEntry {
  return {
    name: 'deep-research', displayName: 'Deep Research', description: LOCAL_DESC,
    version: '0.1.0', icon: '', author: 'Gideon',
    source: '/srv/apps/deep-research', sourceKind: 'local',
    isProvider: false, providerType: '', tags: [],
    permissions: { network: false }, crons: [],
    ...over,
  }
}

const EMPTY: AppCatalog = {
  bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
  localSources: ['/srv/apps'], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
}

function grid(e: AppCatalogEntry, catalog: AppCatalog = { ...EMPTY, localApps: [e] }) {
  return render(
    <StoreView catalog={catalog} result={[{ ...e, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

describe('a Store card names where its bytes came from, before install', () => {
  it('says "local" for a bundle from a folder on this machine', () => {
    grid(entry())
    expect(screen.getByTestId('store-card-origin').textContent).toBe('local')
    expect(screen.getByTestId('store-card-origin').getAttribute('title'))
      .toMatch(/folder on this machine/i)
  })

  it('says "git" for a bundle fetched from a remote', () => {
    const e = entry({
      description: REMOTE_DESC, sourceKind: 'git',
      source: 'https://apps.example.test/bundled.git',
      pointer: 'https://apps.example.test/bundled.git#deep-research',
      permissions: { network: true },
    })
    grid(e, { ...EMPTY, gitApps: [e] })
    expect(screen.getByTestId('store-card-origin').textContent).toBe('git')
    expect(screen.getByTestId('store-card-origin').getAttribute('title'))
      .toMatch(/network/i)
  })

  it('the two copies of one name are TOLD APART on the card, not only in the payload', () => {
    grid(entry())
    const local = screen.getByTestId('store-card-origin').textContent
    screen.getByText(new RegExp(LOCAL_DESC.slice(0, 20)))

    const remote = entry({ description: REMOTE_DESC, sourceKind: 'git', source: 'https://github.com/acme/apps.git' })
    grid(remote, { ...EMPTY, gitApps: [remote] })
    const labels = screen.getAllByTestId('store-card-origin').map((n) => n.textContent)
    expect(labels).toContain('git')
    expect(local).toBe('local')
  })

  it('says nothing at all when the origin is unknown — an absent fact is not a claim', () => {
    const e = entry({ sourceKind: '' as AppCatalogEntry['sourceKind'] })
    grid(e)
    expect(screen.queryByTestId('store-card-origin')).toBeNull()
  })
})

describe('the Store discloses which hosts a listing read contacts', () => {
  it('names the hosts when a network source is listed', () => {
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: ['https://github.com/acme/apps.git'], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    const text = screen.getByTestId('store-egress-disclosure').textContent ?? ''
    expect(text.replace(/\s+/g, ' ')).toMatch(/contacts github\.com/)
  })

  it('says nothing when every source is on this machine — no warning about nothing', () => {
    render(<SourcesPanel catalog={{ ...EMPTY, networkSources: [] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.queryByTestId('store-egress-disclosure')).toBeNull()
  })

  it('points at the off switch for a source with no row to remove', () => {
    const url = 'https://apps.example.test/bundled.git'
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: [url], defaultGitSources: [url], builtinGitSources: [url], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.queryByRole('button', { name: /Remove source/i })).toBeNull()
    const text = (screen.getByTestId('store-egress-disclosure').textContent ?? '').replace(/\s+/g, ' ')
    expect(text).toMatch(/no remove button; turn it off in Settings → Apps/)
  })

  it('does not mention an off switch when no shipped source is listed', () => {
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: ['https://github.com/acme/apps.git'], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.getByRole('button', { name: /Remove source/i })).toBeTruthy()
    expect(screen.getByTestId('store-egress-disclosure').textContent).not.toMatch(/no remove button/)
  })
})
