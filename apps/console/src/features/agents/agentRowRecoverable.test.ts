import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('an agent row hands over the whole of what it clips', () => {
  const PAGE = strip(read('features/agents/AgentsListPage.tsx'))

  it("the agent row's name and description both carry a title", () => {
    expect(PAGE).toMatch(/className="truncate text-on-surface text-\[0\.9375rem\] font-mono" style=\{fvs\(500\)\} title=\{agent\.name\}>\{agent\.name\}<\/span>/)
    expect(PAGE).toMatch(/<span className="truncate" title=\{agent\.description\}>\{agent\.model \? '· ' : ''\}\{agent\.description\}<\/span>/)
  })

  it('the discovered row variant carries them too', () => {
    expect(PAGE).toMatch(/className="block truncate text-on-surface text-\[0\.9375rem\]" style=\{fvs\(500\)\} title=\{agent\.name\}>\{agent\.name\}<\/span>/)
    expect(PAGE).toMatch(/className="mt-0\.5 truncate text-on-surface-low text-\[0\.8125rem\]" title=\{agent\.description\}>\{agent\.description\}<\/p>/)
  })

  it('all four are present — neither variant is half-done', () => {
    // Panel titles also name their agents. Count only the two row constructs under test.
    const rows = ['NativeRow', 'DiscoveredRow'].map(name => {
      const source = PAGE.match(new RegExp(`function ${name}\\([\\s\\S]*?(?=\\nfunction |$)`))?.[0] ?? ''
      expect(source, `found ${name}`).not.toBe('')
      expect((source.match(/title=\{agent\.name\}/g) || []).length, `${name} name`).toBe(1)
      expect((source.match(/title=\{agent\.description\}/g) || []).length, `${name} description`).toBe(1)
      return source
    }).join('\n')
    expect((rows.match(/title=\{agent\.name\}/g) || []).length, 'names').toBe(2)
    expect((rows.match(/title=\{agent\.description\}/g) || []).length, 'descriptions').toBe(2)
  })

  it('the description title excludes the row-added separator', () => {
    expect(PAGE, 'the separator stays in the rendered text only').toMatch(/title=\{agent\.description\}>\{agent\.model \? '· ' : ''\}/)
    expect(PAGE, 'and never inside the title').not.toMatch(/title=\{`?\$?\{?agent\.model \? '· '/)
  })

  it('all four still truncate — the fix is recovery, not re-layout', () => {
    expect((PAGE.match(/truncate/g) || []).length, 'truncating elements in this file').toBeGreaterThanOrEqual(4)
  })

  it('the row still names itself for assistive tech — the half that already worked', () => {
    expect((PAGE.match(/<ListRow[^>]*label=\{agent\.name\}/g) || []).length, 'rows naming themselves').toBe(2)
  })
})
