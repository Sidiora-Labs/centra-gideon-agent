import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalog, AppCatalogEntry } from '../../shared/data/api'
import { StoreDetailPanel, StoreView } from './AppsSection'

const reason = 'This listing downloads from a private network and cannot be installed.'
const entry: AppCatalogEntry = {
  name: 'restricted-app', displayName: 'Restricted app', description: 'An app listing',
  version: '1.0.0', icon: '', author: 'Example',
  source: 'https://registry.example.test/apps.git', sourceKind: 'git',
  pointer: 'https://10.20.30.40/app.git', listedBy: 'https://registry.example.test/apps.git',
  isProvider: false, providerType: '', tags: [], permissions: {}, crons: [],
  installable: false, refused: reason,
}
const item = { ...entry, installed: false, enabled: false, hasUI: false }
const catalog: AppCatalog = {
  bundled: [], gitSources: [entry.source], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

describe('restricted app listings', () => {
  it('keeps the card visible and disables installation', () => {
    const actions: string[] = []
    const recordAction = () => { actions.push('invoked') }
    render(<StoreView catalog={catalog} result={[item]} totalKnown={1} installedCount={0}
      onInstalled={recordAction} reloadCatalog={recordAction} onClearFilters={recordAction}
      filtersActive={false} onOpen={recordAction} onAction={recordAction} onOpenSources={recordAction} />)
    expect(screen.getByText(reason)).toBeTruthy()
    const button = screen.getByRole('button', { name: /^Install$/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    button.click()
    expect(actions).toEqual([])
  })

  it('shows the same refusal in details with no enabled install action', () => {
    const installed: string[] = []
    render(<StoreDetailPanel item={item} onInstalled={() => { installed.push(item.name) }} />)
    expect(screen.getByRole('status').textContent).toContain(reason)
    const button = screen.getByRole('button', { name: /^Install$/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    button.click()
    expect(installed).toEqual([])
  })
})
