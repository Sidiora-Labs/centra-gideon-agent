import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const wrapsRowsOnAContainerSurface = (panel: string) => {
  expect(read(`features/settings/${panel}.tsx`), `${panel} must wrap its rows in RowGroup`)
    .toMatch(/<RowGroup[\s>]/)
  const rowGroup = read('features/settings/settingsUI.tsx').match(/export function RowGroup\([\s\S]*?\n\}/)?.[0] ?? ''
  expect(rowGroup, 'RowGroup must exist to be the wrapper').toContain('Surface')
  expect(rowGroup, 'and it must still paint bg-surface-container — the reason surface="high" is needed')
    .toMatch(/tone="container"/)
}

describe('a settings field on a container backdrop lifts its surface', () => {
  it('SourcesPanel scratchpad field passes surface="high"', () => {
    const src = read('features/settings/SourcesPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,260}?placeholder="~\/notes\/today\.md"/)?.[0] ?? ''
    expect(tag, 'the scratchpad field must exist').toContain('<TextInput')
    expect(tag, 'it sits in a bg-surface-container row, so it must lift off it').toContain('surface="high"')
  })

  it('the SourcesPanel row really is a container-surfaced wrapper', () => {
    wrapsRowsOnAContainerSurface('SourcesPanel')
  })

  it('PacksPanel TextRow passes surface="high"', () => {
    const src = read('features/settings/PacksPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,300}?onKeyDown/)?.[0] ?? ''
    expect(tag, 'the shared TextRow field must exist').toContain('<TextInput')
    expect(tag).toContain('surface="high"')
  })

  it('PacksPanel TextRow callers sit on a container surface', () => {
    wrapsRowsOnAContainerSurface('PacksPanel')
  })

  it('CompanionPanel instance-name field passes surface="high"', () => {
    const src = read('features/settings/CompanionPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,200}?placeholder="e\.g\. Living room Mac"/)?.[0] ?? ''
    expect(tag, 'the instance-name field must exist').toContain('<TextInput')
    expect(tag).toContain('surface="high"')
  })

  it('the CompanionPanel row really is a container-surfaced wrapper', () => {
    wrapsRowsOnAContainerSurface('CompanionPanel')
  })

  it('TextInput still defaults to the container surface, and preserves visible field and focus affordances', () => {
    const forms = read('shared/ui/forms.tsx')
    expect(forms, 'default surface').toMatch(/surface = 'container'/)
    expect(forms, 'container maps to the same token the wrappers use').toMatch(/container: 'bg-surface-container'/)
    expect(forms, 'high is a distinct step').toMatch(/high: 'bg-surface-high'/)
    const base = forms.match(/const fieldChrome = '[^']*'/)?.[0] ?? ''
    expect(base, 'fieldChrome must exist').toContain('fieldChrome')
    expect(/\bborder\b|ring-1/.test(base.replace(/focus:[^\s']*/g, '')),
      'native fields have an at-rest outline in addition to distinct surface fills').toBe(true)
    expect(base).toContain('focus:ring-2')
    expect(forms).toContain('cx(fieldChrome,')
  })

  it('the pre-fix shape does not come back at either site', () => {
    const sources = read('features/settings/SourcesPanel.tsx')
    const packs = read('features/settings/PacksPanel.tsx')
    expect(/<TextInput value=\{scratchpad\} onChange=\{setScratchpad\} mono placeholder/.test(sources)).toBe(false)
    expect(/<TextInput value=\{draft\} onChange=\{setDraft\} placeholder=\{placeholder\} ariaLabel=\{label\} mono\n/.test(packs)).toBe(false)
  })
})
