import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const WEB = process.cwd()

function shippedPanelIds(): string[] {
  const src = readFileSync(join(WEB, 'src/features/settings/SettingsPage.tsx'), 'utf8')
  return [...src.matchAll(/^\s*\{ id: '([a-z-]+)',/gm)].map((m) => m[1])
}

function manifestPanelIds(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const SETTINGS_PANELS = \[([\s\S]*?)\] as const/)
  expect(block, 'SETTINGS_PANELS not found in e2e/routes.ts').toBeTruthy()
  return [...block![1].matchAll(/'([a-z-]+)'/g)].map((m) => m[1])
}

describe('every settings panel is in the axe manifest', () => {
  const shipped = shippedPanelIds()
  const manifest = manifestPanelIds()

  it('finds real panels on both sides (not vacuously green)', () => {
    expect(shipped.length, 'the SUBPAGES matcher must find the panels').toBeGreaterThan(25)
    expect(manifest.length, 'the manifest matcher must find its ids').toBeGreaterThan(25)
  })

  it('has no panel missing from the manifest', () => {
    const missing = shipped.filter((id) => !manifest.includes(id))
    expect(
      missing,
      'These settings panels ship but would NEVER be scanned by axe — each is a route the\n' +
        'gate does not visit. Add them to SETTINGS_PANELS in web/e2e/routes.ts:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
  })

  it('has no manifest entry for a panel that no longer exists', () => {
    const stale = manifest.filter((id) => !shipped.includes(id))
    expect(
      stale,
      'These manifest ids have no matching panel — the scan would hit the settings home\n' +
        'and report a false pass:\n  ' + stale.join('\n  '),
    ).toEqual([])
  })

  it('agrees on order, so the two lists read as one', () => {
    expect(manifest).toEqual(shipped)
  })
})
