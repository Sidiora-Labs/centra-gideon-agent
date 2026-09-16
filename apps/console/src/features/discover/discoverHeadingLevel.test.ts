import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe("Discover's area headings sit one rung under the page title", () => {
  const PAGE = strip(read('features/discover/DiscoverPage.tsx'))

  it('the area heading is an h2', () => {
    expect(PAGE).toMatch(/<h2 data-type="label-l" className="text-on-surface-var">\{group\.area\}<\/h2>/)
  })

  it('no h3 remains on the page', () => {
    expect(PAGE, 'a page-level section may not skip to h3').not.toMatch(/<h3[\s>]/)
  })

  it('the page still renders exactly one h1, through PageTitle', () => {
    expect(PAGE).toMatch(/<PageTitle/)
    expect(PAGE, 'and no hand-rolled h1 competing with it').not.toMatch(/<h1[\s>]/)
  })

  it('the type still comes from data-type, so the tag change is invisible', () => {
    expect(PAGE).toMatch(/data-type="label-l"/)
  })

  it('the h3s that remain in the tree are all panel-level or markdown — the scope claim', () => {
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    const withH3 = walk(SRC)
      .filter((abs) => /<h3[\s>]/.test(strip(readFileSync(abs, 'utf8'))))
      .map((abs) => abs.replace(SRC + '/', ''))
    expect(withH3.sort(), 'files still using h3').toEqual([
      'features/code/CodeCockpitPage.tsx',
      'features/settings/VoicePanel.tsx',
      'features/settings/VoiceProfilesSection.tsx',
      'features/workflows/IntrospectPanel.tsx',
      'features/workflows/LedgerRailsPanel.tsx',
      'features/workflows/NodeInspectorDrawer.tsx',
      'features/workflows/OutboxPanel.tsx',
      'features/workflows/WorkspacePanel.tsx',
      'shared/ui/Markdown.tsx',
    ])
  })
})
