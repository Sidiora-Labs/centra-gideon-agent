import { jsxTags } from '../../shared/testing/jsxContracts'
import { namedOwner } from '../../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
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

  it('the native Discover owner keeps its page-to-area outline', () => {
    const owner = namedOwner(PAGE, 'DiscoverPage')
    expect(jsxTags(owner, ['PageTitle'])).toHaveLength(1)
    expect(jsxTags(owner, ['h1', 'h3'])).toHaveLength(0)
    const areas = jsxTags(owner, ['h2']).filter(tag => tag.element.includes('{group.area}'))
    expect(areas).toHaveLength(1)
    expect(areas[0].attributes.get('data-type')).toBe('"label-l"')
    expect(PAGE).toContain("import { PageTitle } from '../../shared/ui/PageTitle'")
    const nativeTitle = namedOwner(read('shared/ui/PageTitle.tsx'), 'PageTitle')
    expect(jsxTags(nativeTitle, ['h1'])).toHaveLength(1)
  })
})
