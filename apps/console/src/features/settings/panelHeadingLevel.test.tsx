import ts from 'typescript'
import { jsxTags } from '../../shared/testing/jsxContracts'
import { sourceFile } from '../../shared/testing/sourceOwners'
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
    const missing = files.filter((f) => {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      if (jsxTags(src, nativeBindings(src, 'PanelHeader')).length > 0) return false
      if (f !== 'HypermidPanel.tsx') return true
      const branches = ['HypermidOverview', 'RemoteAccess', 'Connections', 'Security', 'Operations', 'Lifecycle']
      for (const branch of branches) {
        expect(nativeBindings(src, branch, `../hypermid/${branch}`)).toEqual([branch])
        expect(jsxTags(src, [branch])).toHaveLength(1)
        const child = readFileSync(join(SETTINGS, '../hypermid', `${branch}.tsx`), 'utf8')
        const headers = jsxTags(child, nativeBindings(child, 'PanelHeader', '../settings/settingsUI'))
        const headings = jsxTags(child, ['h1']).filter(tag => tag.attributes.get('data-type') === '"title-l"')
        expect(headers.length + headings.length, `${branch} owns its page heading`).toBe(1)
      }
      expect(jsxTags(src, ['h1']).map(tag => tag.attributes.get('data-type'))).toEqual(['"title-l"', '"title-l"'])
      return false
    })
    expect(missing, 'a settings sub-route is a page; its panel title is its h1').toEqual([])
  })

  it("the inbox DRAWER copy does not use PanelHeader — #/inbox already has an h1", () => {
    const drawer = readFileSync(join(process.cwd(), "src/features/inbox/InboxSettingsPanel.tsx"), 'utf8')
    expect(drawer.length, 'the drawer copy must exist').toBeGreaterThan(500)
    expect(/<PanelHeader\b/.test(drawer), 'the embedded copy must not emit a page-level heading').toBe(false)
  })
})

function nativeBindings(src: string, symbol: string, module = './settingsUI'): string[] {
  return sourceFile(src).statements.flatMap(statement => {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier) || statement.moduleSpecifier.text !== module) return []
    const bindings = statement.importClause?.namedBindings
    return bindings && ts.isNamedImports(bindings) ? bindings.elements.filter(binding => (binding.propertyName ?? binding.name).text === symbol).map(binding => binding.name.text) : []
  })
}
