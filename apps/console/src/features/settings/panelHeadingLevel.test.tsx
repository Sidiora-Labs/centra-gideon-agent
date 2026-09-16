import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PanelHeader, Section } from './settingsUI'


const SETTINGS = join(process.cwd(), "src/features/settings")

describe('PanelHeader is the page heading of a settings sub-route', () => {
  it('renders an h1', () => {
    const { container } = render(<PanelHeader title="Memory" hint="how memory works" />)
    const h = container.querySelector('h1')
    expect(h, 'the panel title is the top-level heading of its page').toBeTruthy()
    expect(h!.textContent).toBe('Memory')
    expect(h!.getAttribute('data-type'), 'size comes from the type role, not the tag').toBe('title-l')
    expect(container.querySelector('h2'), 'and it must not ALSO emit a level-2').toBeNull()
  })

  it('Section renders an h2, so the outline does not skip a level', () => {
    const { container } = render(<Section title="Retention"><p>x</p></Section>)
    expect(container.querySelector('h2')?.textContent).toBe('Retention')
    expect(container.querySelector('h3'), 'h3 under an h1 title skips a level').toBeNull()
  })

  it('EVERY settings panel uses it — the count floor was too loose', () => {
    const files = readdirSync(SETTINGS).filter((f) => /Panel\.tsx$/.test(f))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    const missing = files.filter((f) => !/<PanelHeader\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(missing, 'a settings sub-route is a page; its panel title is its h1').toEqual([])
  })

  it("the inbox DRAWER copy does not use PanelHeader — #/inbox already has an h1", () => {
    const drawer = readFileSync(join(process.cwd(), "src/features/inbox/InboxSettingsPanel.tsx"), 'utf8')
    expect(drawer.length, 'the drawer copy must exist').toBeGreaterThan(500)
    expect(/<PanelHeader\b/.test(drawer), 'the embedded copy must not emit a page-level heading').toBe(false)
  })
})
