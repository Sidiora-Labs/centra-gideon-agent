import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { connectorWarning } from './PacksPanel'


describe('the pack row says what is missing, in words', () => {
  it('names a single skipped connector without its prefix', () => {
    expect(connectorWarning(['connector_missing:health-records'])).toBe('Needs a connector: health-records')
  })

  it('pluralises and lists when several are skipped', () => {
    expect(connectorWarning(['connector_missing:health-records', 'connector_missing:calendar']))
      .toBe('Needs connectors: health-records, calendar')
  })

  it('says nothing when nothing is missing', () => {
    expect(connectorWarning([])).toBeUndefined()
  })

  it('shows an unrecognised marker verbatim instead of swallowing it', () => {
    expect(connectorWarning(['connector_broken:foo'])).toBe('connector_broken:foo')
    expect(connectorWarning(['connector_missing:a', 'something_else']))
      .toBe('Needs a connector: a · something_else')
  })

  it('never renders the raw prefix for a marker it understands', () => {
    for (const markers of [
      ['connector_missing:health-records'],
      ['connector_missing:a', 'connector_missing:b'],
    ]) {
      expect(connectorWarning(markers)).not.toContain('connector_missing:')
    }
  })

  it('the row renders through this function, and the old template is gone', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/PacksPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src).toMatch(/hint=\{connectorWarning\(pack\.connector_markers\)\}/)
    expect(src, 'the raw join must not come back').not.toMatch(/Unavailable: \$\{pack\.connector_markers/)
  })
})
