import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const HERE = join(process.cwd(), "src/features/settings")
const read = (f: string) => readFileSync(join(HERE, f), 'utf8')

const SITES = [
  { file: 'MemoryPanel.tsx', fetch: 'api.memoryEvents({ limit: 100 })', announce: /<LoadError what="memory audit log"/ },
  { file: 'MemoryPanel.tsx', fetch: 'api.memorySemantic()', announce: /<LoadError what="memories"/ },
  { file: 'ModelBackends.tsx', fetch: 'api.modelProviders()', announce: /<InlineError icon className="mb-3">/ },
  { file: 'ProjectionRulesPanel.tsx', fetch: 'api.projectionRules()', announce: /<InlineError icon>/ },
]

describe('a settings list distinguishes a failed load from an empty one', () => {
  for (const s of SITES) {
    it(`${s.file} lets the rejection reach the hook`, () => {
      const src = read(s.file)
      expect(src, 'the fetcher must be bare — a `.catch` here makes the error branch unreachable')
        .toContain(`${s.fetch},`)
      const swallowed = new RegExp(`${s.fetch.replace(/[.()[\]{}]/g, '\\$&')}\\s*\\.catch`)
      expect(swallowed.test(src), `${s.file} still substitutes a value for the rejection`).toBe(false)
    })

    it(`${s.file} renders an ANNOUNCED failure, not a quiet empty line`, () => {
      expect(read(s.file)).toMatch(s.announce)
    })

    it(`${s.file} reads the error off the hook`, () => {
      expect(read(s.file)).toMatch(/\berror\s*[,}]|error:\s*\w*(?:err|Err)\w*/)
    })
  }

  it('the failure branch precedes the empty branch at each site', () => {
    const mem = read('MemoryPanel.tsx')
    const auditTab = mem.slice(mem.indexOf('function AuditTab()'), mem.indexOf('function AuditRow'))
    expect(auditTab.search(/<LoadError\b/), 'the memory Audit tab guards before it skeletons')
      .toBeLessThan(auditTab.search(/<ListSkeleton\b/))

    const backends = read('ModelBackends.tsx')
    expect(backends.search(/<InlineError\b/)).toBeLessThan(backends.search(/<RemoteProvidersSkeleton\s*\/>/))

    const rules = read('ProjectionRulesPanel.tsx')
    const chain = rules.slice(rules.indexOf('rules === undefined'))
    expect(chain.search(/<InlineError\b/)).toBeLessThan(chain.search(/<ListSkeleton\b/))
    expect(chain.search(/<ListSkeleton\b/), 'and the loading branch precedes the empty one')
      .toBeLessThan(chain.search(/No custom rules/))
  })

  it('the projection panel gained a loading state it never had', () => {
    const src = read('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/rules === undefined \? \(\s*<ListSkeleton/)
  })

  it('ModelBackends KEEPS the models catch — that one is a real distinction', () => {
    expect(read('ModelBackends.tsx')).toMatch(/api\.modelsAvailable\(\)\.catch\(\(\) => \[\] as/)
  })

  it('the settings HUB does not poison the keys these panels now guard', () => {
    const widgets = readFileSync(join(HERE, 'settingsWidgets.tsx'), 'utf8')
    for (const key of ['settings:archives', 'settings:projection-rules']) {
      const call = widgets.slice(widgets.indexOf(`'${key}'`), widgets.indexOf(`'${key}'`) + 160)
      expect(call, `the hub tile for ${key} must not substitute a value`).not.toMatch(/\.catch\(\(\) =>/)
    }
  })

  it('the census that found these five is reproducible, and its population is stated', () => {
    const panels = readdirSync(HERE).filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    expect(panels.length, 'the scan must find the settings panels').toBeGreaterThan(25)
    const code = (n: string) => readFileSync(join(HERE, n), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const swallowers = panels.filter((n) =>
      /useQuery[\s\S]{0,240}?\.catch\(\(\) =>\s*(\[\]|null|undefined|\{\})/.test(code(n)),
    )
    expect(swallowers, 'the five list bodies must no longer be among them').not.toContain('ArchivePanel.tsx')
    expect(swallowers).not.toContain('AuditPanel.tsx')
    expect(swallowers).not.toContain('ProjectionRulesPanel.tsx')
    expect(swallowers.length, 'if this moves, say which way and why in the PR').toBeLessThanOrEqual(17)
  })
})
