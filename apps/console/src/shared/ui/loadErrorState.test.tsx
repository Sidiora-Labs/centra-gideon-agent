import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { LoadError, EmptyState } from './ListScaffold'
import ts from 'typescript'
import { swallowedReads } from '../testing/swallowCensus'
import swallowBudget from '../testing/swallowBudget.json'


describe('LoadError announces and offers recovery', () => {
  it('is an alert — a load failure interrupts', () => {
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('[role="alert"]'), 'a failed load must be announced').not.toBeNull()
  })

  it('EmptyState is NOT an alert — "you have none" is a normal answer', () => {
    const { container } = render(<EmptyState title="No projects yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it("names what failed, so the message isn't generic", () => {
    render(<LoadError what="projects" />)
    expect(screen.getByRole('heading', { name: /Couldn't load your projects/ })).toBeTruthy()
  })

  it("surfaces the server's own message when there is one", () => {
    render(<LoadError what="projects" error={new Error('gateway timed out')} />)
    expect(screen.getByText('gateway timed out')).toBeTruthy()
  })

  it('falls back to a reassuring line when the error has no message', () => {
    render(<LoadError what="projects" error={{}} />)
    expect(screen.getByText(/this is just a load error, and nothing was lost/)).toBeTruthy()
  })

  it('the fallback reads grammatically for a SINGULAR noun too', () => {
    render(<LoadError what="project" error={{}} />)
    expect(screen.getByText(/Couldn't load your project/)).toBeTruthy()
    expect(screen.queryByText(/are safe/), 'the old plural-only copy is gone').toBeNull()
  })

  it('offers a retry that re-runs the fetch', () => {
    const onRetry = vi.fn()
    render(<LoadError what="projects" error={new Error('x')} onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('omits the retry button when the surface cannot retry', () => {
    render(<LoadError what="projects" error={new Error('x')} />)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('hides the decorative icon from assistive tech', () => {
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
})


const SRC = join(process.cwd(), "src")
const codeOf = (abs: string) =>
  readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const SWALLOW = /\.catch\(\(\)\s*=>\s*(\[\]|null|undefined|\{\})/

function cachedCalls(src: string): { key: string; args: string; line: number }[] {
  const out: { key: string; args: string; line: number }[] = []
  for (const m of src.matchAll(/useQuery(?:<[^>]*>)?\(/g)) {
    const start = (m.index ?? 0) + m[0].length
    let i = start
    let depth = 1
    while (i < src.length && depth > 0) {
      const c = src[i]
      if (c === '(') depth++
      else if (c === ')') depth--
      i++
    }
    const args = src.slice(start, i - 1)
    const key = args.match(/^\s*'([^']+)'/)?.[1]
    if (key) out.push({ key, args, line: src.slice(0, m.index).split('\n').length })
  }
  return out
}
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated surfaces read the error', () => {
  const ADOPTERS = [
    'features/tasks/TasksListPage.tsx',
    'features/projects/ProjectsSection.tsx',
    'features/code/CodeSection.tsx',
    'features/learning/LearningPage.tsx',
    'features/prompts/PromptsListPage.tsx',
    'features/workflows/WorkflowsListPage.tsx',
    'features/discover/DiscoverPage.tsx',
    'features/apps/AppsSection.tsx',
    'features/settings/InboxSettingsPanel.tsx',
    'features/ChatPage.tsx',
    // region, the custom-rules list) are pinned per site in
    'features/settings/ArchivePanel.tsx',
    'features/settings/AuditPanel.tsx',
    'features/loops/LoopsListPage.tsx',
    'features/code/CodeCockpitPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} branches on the load error before the empty state`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must render the shared primitive').toMatch(/<LoadError\b/)
      expect(src, 'must capture the rejection, not discard it').toMatch(
        /\berror\s*[,}]|error:\s*\w*(?:err|Err)\w*|catch\(\(\w+\)\s*=>\s*\{[^}]*[Ee]rr\w*\(/,
      )
      const errAt = src.search(/<LoadError\b/)
      const loadAt = Math.min(...[/<ListSkeleton\b/, /<Loading\b/, /<FormSkeleton\b/, /<Loader2\b/].map((re) => {
        const i = src.search(re)
        return i === -1 ? Number.POSITIVE_INFINITY : i
      }))
      expect(loadAt, 'the surface must have a loading state at all').toBeLessThan(Number.POSITIVE_INFINITY)
      const errorBranchFirst = errAt < loadAt
      const loadingClearedOnFailure = /finally\s*\{[^}]*setLoading\(false\)/.test(src)
      const loadingFromDataLayer = /loading(?::\s*\w+)?\s*[,}][^\n]*\n?[\s\S]{0,80}?useQuery\(/.test(src)
        || /useQuery\([\s\S]{0,400}?\bloading(?::\s*\w+)?\s*[,}]/.test(src)
      expect(
        errorBranchFirst || loadingClearedOnFailure || loadingFromDataLayer,
        'the error branch must be reachable: it precedes the loading branch, or the loading flag is cleared in a finally, or `loading` comes from the one data layer (which clears it on a rejection by construction) so a failure gets past it',
      ).toBe(true)
    })
  }

  it('no OTHER consumer of an adopter\'s cache key swallows either', () => {
    const files: string[] = []
    for (const abs of walk(SRC)) files.push(abs)
    const consumers = new Map<string, { at: string; swallows: boolean }[]>()
    for (const abs of files) {
      for (const c of cachedCalls(codeOf(abs))) {
        const at = `${abs.slice(SRC.length + 1)}:${c.line}`
        consumers.set(c.key, [...(consumers.get(c.key) ?? []), { at, swallows: SWALLOW.test(c.args) }])
      }
    }
    const appsConsumers = consumers.get('apps') ?? []
    expect(appsConsumers.length, "the scan must find the 'apps' key's consumers").toBeGreaterThanOrEqual(3)

    const adopterKeys = new Set<string>()
    for (const rel of ADOPTERS) {
      for (const c of cachedCalls(codeOf(join(SRC, rel)))) adopterKeys.add(c.key)
    }
    expect(adopterKeys.size, 'the adopters must declare at least one cache key').toBeGreaterThan(0)

    const poisoners: string[] = []
    for (const key of adopterKeys) {
      for (const c of consumers.get(key) ?? []) if (c.swallows) poisoners.push(`${c.at} (key '${key}')`)
    }
    expect(poisoners, 'a swallow here makes every other consumer of the key unable to see the failure').toEqual([])
  })

  it('no adopter swallows the rejection inside its fetcher', () => {
    for (const rel of ADOPTERS) {
      const swallowing = cachedCalls(codeOf(join(SRC, rel))).filter((c) => SWALLOW.test(c.args))
      expect(swallowing.map((c) => `${c.key}:${c.line}`), `${rel} swallows a fetch rejection`).toEqual([])
    }
  })

  it('the primitive is exported from the list kit, beside EmptyState', () => {
    const kit = readFileSync(join(SRC, 'shared/ui/ListScaffold.tsx'), 'utf8')
    expect(kit).toMatch(/export function LoadError\b/)
    expect(kit).toMatch(/export function EmptyState\b/)
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length, 'the walker must find the tree').toBeGreaterThan(200)
  })
})

describe('direct fetches keep their rejection too — the 2026-09-05 false-empty family', () => {
  const PINS: Array<[string, RegExp, string]> = [
    ['features/knowledge/KnowledgeListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*setOutcomesErr\(e\);\s*setOutcomes\(null\)\s*\}\)/, 'gathered matches'],
    ['features/skills/SkillsPage.tsx', /catch\s*\(e\)\s*\{\s*setSearchErr\(e\);\s*setResults\(null\)/, 'skill search results'],
    ['features/tasks/TasksListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*setReadyErr\(e\);\s*setReady\(null\)\s*\}\)/, 'ready tasks'],
    ['features/tasks/TasksListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*if\s*\(alive\)\s*\{\s*setSearchErr\(e\);\s*setResults\(null\)\s*\}\s*\}\)/, 'search results'],
  ]

  it('each slice records its rejection and renders LoadError for it', () => {
    for (const [rel, catchPin, what] of PINS) {
      const src = codeOf(join(SRC, rel))
      expect(catchPin.test(src), `${rel}: the catch must record the error, not fold it into an empty value (${catchPin})`).toBe(true)
      expect(src.includes(`<LoadError what="${what}"`), `${rel}: must render <LoadError what="${what}">`).toBe(true)
    }
  })
})


describe('full-file swallow census', () => {
  const surfaces = [
    'features/skills/LearningSummaryBlock.tsx',
    'features/skills/SkillInspector.tsx',
    'features/chat/PromptPalette.tsx',
    'features/chat/SessionSkillsReview.tsx',
    'features/dashboard/widgets/Suggestions.tsx',
    'features/knowledge/TagManager.tsx',
    'features/knowledge/ConflictPanel.tsx',
    'features/knowledge/RestructureControl.tsx',
    'features/knowledge/KnowledgeCreatePage.tsx',
    'features/settings/PromptsPanel.tsx',
    'features/settings/NotificationsPanel.tsx',
    'features/settings/ProviderConfigForm.tsx',
    'features/settings/DiagnosticsPanel.tsx',
    'features/settings/AlwaysOnConventions.tsx',
    'features/tasks/TaskCreatePage.tsx',
    'features/triggers/TriggersListPage.tsx',
  ]

  it('finds direct reads throughout a file, including cast and parenthesized fallbacks', () => {
    const source = `
      const cached = useQuery('safe', () => api.safe())
      function direct() { return api.read().catch(() => [] as Row[]) }
      function later() { return api.read().catch(() => ({} as Record<string, unknown>)) }
      const nullable = api.read().catch(() => null as Value | null)
      const block = api.read().catch(() => { return ([] as Row[]) })
      const discarded = api.read().catch(() => {})
      const state = api.read().catch(() => setRows([]))
    `
    expect(swallowedReads(source)).toHaveLength(6)
    expect(swallowedReads(source).every((site) => site.line > 2)).toBe(true)
  })

  it('does not count strings, comments, propagated failures or explicit error state', () => {
    expect(swallowedReads(`
      // api.read().catch(() => [])
      const text = "api.read().catch(() => [])"
      api.read().catch((error) => { throw error })
      api.read().catch(setLoadError)
      api.read().catch((error) => { setLoadError(error); setRows(null) })
    `)).toEqual([])
  })

  it('ratchets the measured remaining whole-file budget without hiding its population', () => {
    const files = walk(SRC)
    expect(files.length).toBeGreaterThanOrEqual(350)
    expect(Object.keys(swallowBudget)).toHaveLength(61)
    expect(Object.values(swallowBudget).reduce((sum, count) => sum + count, 0)).toBe(173)
    const growth: string[] = []
    for (const abs of files) {
      const rel = abs.slice(SRC.length + 1)
      const sites = swallowedReads(readFileSync(abs, 'utf8'))
      const allowed = (swallowBudget as Record<string, number>)[rel] ?? 0
      if (sites.length > allowed) growth.push(`${rel}: ${sites.length} > ${allowed}: ${sites.map((s) => s.line).join(', ')}`)
    }
    expect(growth).toEqual([])
  })

  for (const rel of surfaces) {
    it(`${rel} keeps read failures visible and has no silent fallback`, () => {
      const source = readFileSync(join(SRC, rel), 'utf8')
      expect(swallowedReads(source)).toEqual([])
      expect(source).toMatch(/<LoadError[^>]*error=\{/)
      expect(source).toMatch(/error:\s*\w+Err|catch\(set\w*Err\)|catch\(\(error\)/)
      const parsed = ts.createSourceFile(rel, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
      expect((parsed as typeof parsed & { parseDiagnostics: unknown[] }).parseDiagnostics).toEqual([])
    })
  }

  it('does not let the shared prompt tile poison the repaired panel cache', () => {
    const source = readFileSync(join(SRC, 'features/settings/settingsWidgets.tsx'), 'utf8')
    expect(source).toMatch(/'settings:prompt-bindings', \(\) => api\.promptBindings\(\),/)
    expect(source).toContain('error: bErr')
    expect(source).toContain('Couldn’t load prompt bindings.')
  })
})
