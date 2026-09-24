import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { registryProvenance } from '../../shared/data/provenance'
import { RegistryProvenanceLine } from './RegistryProvenanceLine'
import { StoreView, type StoreItem } from './AppsSection'

const details = {
  maintainer: 'Example Maintainers',
  lastValidated: '2026-09-24',
  scanVerdict: 'clean',
}

function card(registry: StoreItem['registry']) {
  const item: StoreItem = {
    name: 'registry-app', displayName: 'Registry app', description: 'A listed app',
    version: '1.0.0', icon: '', author: '', source: '/apps', sourceKind: 'local',
    isProvider: false, providerType: '', tags: [], installed: false, enabled: false,
    hasUI: false, registry,
  }
  return render(
    <StoreView catalog={{ bundled: [], gitSources: [], remoteApps: [item] }}
      result={[item]} totalKnown={1} installedCount={0} filtersActive={false}
      onInstalled={() => {}} reloadCatalog={() => {}} onClearFilters={() => {}}
      onOpen={() => {}} onAction={() => {}} onOpenSources={() => {}} />,
  ).container.innerHTML
}

describe('registry provenance on Store cards', () => {
  it('renders the helper-owned wording in the actual card', () => {
    const html = card(details)
    expect(html).toContain('data-testid="store-registry-provenance"')
    expect(html).toContain(registryProvenance(details)!.label)
    expect(html).toContain('Reported by the registry.')
  })

  it.each([undefined, null])('omits provenance for a non-registry card (%s)', value => {
    expect(card(value)).not.toContain('data-testid="store-registry-provenance"')
    expect(registryProvenance(value)).toBeNull()
  })

  it('keeps missing registry facts visible as unknown', () => {
    const html = card({})
    expect(html).toContain('Maintainer: not provided')
    expect(html).toContain('Last validated: not provided')
    expect(html).toContain('Scan: not available')
  })

  it.each(['clean', 'warning', 'dangerous'])('shows the %s verdict without claiming current verification', verdict => {
    render(<RegistryProvenanceLine registry={{ ...details, scanVerdict: verdict }} />)
    const line = screen.getByTestId('store-registry-provenance')
    expect(line.textContent).toBe(registryProvenance({ ...details, scanVerdict: verdict })!.label)
    expect(line.textContent).toContain(`Scan: ${verdict}`)
    expect(line.title).toContain('does not replace the security scan at installation')
  })

  it.each(['', ' ', 'unrecognized'])('does not label an unknown verdict as clean (%s)', verdict => {
    expect(registryProvenance({ scanVerdict: verdict })!.label).toContain('Scan: not available')
  })

  it('escapes registry-provided text', () => {
    const html = card({ ...details, maintainer: '<script>doBadThings()</script>' })
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })
})
