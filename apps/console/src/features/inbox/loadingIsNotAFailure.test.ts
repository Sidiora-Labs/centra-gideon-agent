import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip([rel, ...(rel === 'inbox/InboxSettingsPanel.tsx' ? ['inbox/inboxSettingsState.ts'] : [])].map(path => readFileSync(join(PAGES, path), 'utf8')).join('\n'))

const DRAWER = 'inbox/InboxSettingsPanel.tsx'
const PANEL = 'settings/InboxSettingsPanel.tsx'
const WIDGET = 'settings/settingsWidgets.tsx'

describe('a failed inbox-settings read is reported, not shown as loading', () => {
  it('the drawer copy captures the rejection instead of substituting null', () => {
    const src = read(DRAWER)
    expect(/inboxSettings\(\)[\s\S]{0,80}\.catch\(\(\) => setS\(null\)\)/.test(src),
      'setS(null) leaves !s true, so <Loading /> renders forever').toBe(false)
    expect(src, 'the rejection must land somewhere').toMatch(/\.catch\(setLoadErr\)/)
    expect(src, 'and be rendered').toMatch(/<LoadError what="inbox settings"/)
  })

  it.each([[DRAWER, /if \(!s\) return <Loading what="inbox settings" \/>/], [PANEL, /return <FormSkeleton/]])(
    '%s puts the error branch before its loading gate', (rel, loadingGate) => {
      const src = read(rel)
      const errAt = src.search(/<LoadError\b/)
      const loadAt = src.search(loadingGate)
      expect(errAt, `${rel} must render LoadError`).toBeGreaterThan(-1)
      expect(loadAt, `${rel} must still have a loading gate`).toBeGreaterThan(-1)
      expect(errAt, `${rel}: after the gate, a failure spins forever`).toBeLessThan(loadAt)
    })

  it('the dashboard tile stops shimmering when the read failed', () => {
    const src = read(WIDGET)
    expect(/inboxSettings\(\)[\s\S]{0,90}\.catch\(/.test(src), 'the tile must not swallow the shared key').toBe(false)
    expect(src, 'loading must not include the failed state').toMatch(/loading=\{s === undefined && !inboxErr\}/)
    expect(src, 'and the failure must be visible in the card').toMatch(/Couldn&rsquo;t load inbox settings/)
  })

  it('reads the real files (not vacuously green)', () => {
    for (const rel of [DRAWER, PANEL, WIDGET]) expect(read(rel).length, rel).toBeGreaterThan(1000)
  })
})
