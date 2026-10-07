import ts from 'typescript'
import { namedOwner, nodes, queryRegistration } from '../shared/testing/sourceOwners'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const nav = () => {}
const q = {}
const setQuery = () => {}
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      inbox: () => Promise.resolve([]),
      inboxStatus: () => Promise.resolve({ providers: [], enabled: false }),
      inboxKinds: () => Promise.resolve([]),
      inboxProviders: () => Promise.resolve([]),
      proactiveDigest: () => Promise.resolve({ state: 'uninstalled', enabled: false, installed: false, error: '' }),
      skills: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      learningSummary: () => Promise.resolve({
        window_days: 7, total: 0,
        new_skills: { count: 0, names: [] }, refined_skills: { count: 0, names: [] },
        pending_proposals: { count: 0, names: [] }, facts: { count: 0, names: [] },
      }),
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0 }),
      autonomyLadder: boom,
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/inbox distinguishes a failed read from an empty queue', () => {
  it('shows a retryable LoadError when the items read rejects', async () => {
    mockApi({ inbox: boom })
    const { InboxPage } = await import('./inbox/InboxPage')
    render(<InboxPage query={q} setQuery={setQuery} navigate={nav} />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/inbox/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Inbox zero' }), 'not the caught-up claim').toBeNull()
  })

  it('still shows "Inbox zero" when the queue really is empty', async () => {
    mockApi({})
    const { InboxPage } = await import('./inbox/InboxPage')
    render(<InboxPage query={q} setQuery={setQuery} navigate={nav} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Inbox zero' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty queue is not an error').toBeNull()
  })
})

describe('#/skills distinguishes a failed read from an empty library', () => {
  it('shows a retryable LoadError when the installed read rejects', async () => {
    mockApi({ skills: boom })
    const { SkillsPage } = await import('./skills/SkillsPage')
    render(<SkillsPage query={q} setQuery={setQuery} />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/skills/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No skills installed' }), 'not the newcomer claim').toBeNull()
  })

  it('still shows "No skills installed" when nothing is installed', async () => {
    mockApi({})
    const { SkillsPage } = await import('./skills/SkillsPage')
    render(<SkillsPage query={q} setQuery={setQuery} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No skills installed' })).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('#/skills → Proposals distinguishes a failed read from no proposals', () => {
  it('shows a retryable LoadError when the proposals read rejects', async () => {
    mockApi({ skillProposals: boom })
    const { SkillProposals } = await import('./skills/SkillProposals')
    render(<SkillProposals />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/proposals/i)
    expect(screen.queryByRole('heading', { name: 'No skill proposals' }), 'not a claim about the synthesizer').toBeNull()
  })

  it('still shows "No skill proposals" when there are none', async () => {
    mockApi({})
    const { SkillProposals } = await import('./skills/SkillProposals')
    render(<SkillProposals />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No skill proposals' })).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('#/knowledge distinguishes a failed read from an empty library', () => {
  function mockStore(over: Record<string, unknown>) {
    vi.doMock('./knowledge/knowledgeStore', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      listKnowledge: () => Promise.resolve([]),
      knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0, embedded: 0 }),
      ...over,
    }))
  }
  const mountKnowledge = async () => {
    const { KnowledgeListPage } = await import('./knowledge/KnowledgeListPage')
    render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}}
      query={q} setQuery={setQuery} />)
  }

  it('shows a retryable LoadError instead of a blank region when the items read rejects', async () => {
    mockApi({}); mockStore({ listKnowledge: boom })
    await mountKnowledge()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/knowledge items/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('still shows the newcomer empty state when the library really is empty', async () => {
    mockApi({}); mockStore({})
    await mountKnowledge()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Knowledge base is empty' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty library is not an error').toBeNull()
  })
})

describe('the sources no longer swallow their own error', () => {
  const read = (p: string) => readFileSync(join(process.cwd(), "src", p), 'utf8')

  const registration = (src: string, key: string) => {
    return queryRegistration(src, key)
  }

  it('the inbox items read lets its rejection through', () => {
    const src = read('features/inbox/inboxQueueState.ts')
    expect(read('features/inbox/InboxPage.tsx')).toContain('useInboxQueue')
    expect(registration(src, "'inbox:items'")).not.toMatch(/\.catch\(/)
    expect(read('features/inbox/InboxPage.tsx'), 'and the error gates the LoadError').toMatch(/items === undefined && itemsErr/)
  })

  it('the installed-skills read lets its rejection through', () => {
    const src = read('features/skills/SkillsPage.tsx')
    expect(registration(src, "'skills'")).not.toMatch(/\.catch\(/)
    expect(src, 'and the error gates the LoadError').toMatch(/items === undefined && itemsErr/)
  })

  it('the knowledge items read lets BOTH branches through', () => {
    const src = read('features/knowledge/KnowledgeListPage.tsx')
    const reg = registration(src, 'itemsKey')
    expect(reg, 'the shelf branch must not swallow').not.toMatch(/\.catch\(\(\)\s*=>\s*\[\]/)
    expect(reg, 'the search branch never did').toContain('listKnowledge')
    expect(src, 'and the error is rendered, not dropped to a blank region')
      .toMatch(/itemsData === undefined && itemsErr/)
  })
})

describe("the what= values compose LoadError's sentence", () => {
  const SITES = [
    'features/inbox/InboxPage.tsx',
    'features/skills/SkillsPage.tsx',
    'features/skills/SkillProposals.tsx',
    'features/knowledge/KnowledgeListPage.tsx',
    'features/tasks/TasksListPage.tsx',
  ]
  it('reads as a sentence in both of the primitive\'s templates', () => {
    let checked = 0
    for (const f of SITES) {
      const src = readFileSync(join(process.cwd(), "src", f), 'utf8')
      const sites = nodes(src, node => (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) && node.tagName.getText() === 'LoadError')
      expect(sites.length, `${f}: LoadError sites must exist`).toBeGreaterThan(0)
      for (const node of sites) {
        if (!ts.isJsxSelfClosingElement(node) && !ts.isJsxOpeningElement(node)) continue
        const what = node.attributes.properties.find(prop => ts.isJsxAttribute(prop) && prop.name.getText() === 'what')
        expect(what).toBeDefined()
        if (!what || !ts.isJsxAttribute(what) || !what.initializer) throw new Error('LoadError missing what')
        const values = ts.isStringLiteral(what.initializer) ? [what.initializer.text] : nodes(what.initializer.getText(), ts.isStringLiteral).map(value => (value as ts.StringLiteral).text)
        expect(values.length).toBeGreaterThan(0)
        for (const v of values) {
          checked++
          expect(v, `Couldn't load your ${v}`).not.toMatch(/^(the|this|a|an) /)
          expect(v).toEqual(v.toLowerCase())
        }
      }
    }
    expect(checked, 'each named destination supplies meaningful error subjects').toBeGreaterThanOrEqual(SITES.length)
    const missingWhat = nodes('<LoadError error={error} />', node => ts.isJsxSelfClosingElement(node) && node.tagName.getText() === 'LoadError')[0]
    expect(ts.isJsxSelfClosingElement(missingWhat) && missingWhat.attributes.properties.some(prop => ts.isJsxAttribute(prop) && prop.name.getText() === 'what')).toBe(false)
    const primitive = readFileSync(join(process.cwd(), 'src/shared/ui/ListScaffold.tsx'), 'utf8')
    expect(namedOwner(primitive, 'LoadError')).toContain("Couldn't load your {what}")
    expect(namedOwner(primitive, 'LoadError')).not.toContain('are safe')
  })
})

describe('every useQuery list destination adopts LoadError', () => {
  it('has no un-named holdouts', () => {
    const dir = join(process.cwd(), "src/features")
    const files: string[] = []
    const walk = (d: string) => {
      for (const e of require('node:fs').readdirSync(d, { withFileTypes: true })) {
        const p = join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (e.name.endsWith('.tsx') && !e.name.includes('.test.')) files.push(p)
      }
    }
    walk(dir)

    const HOOK = /useQuery(?:<[\s\S]*?>)?\(/
    const usesHook = files.filter((f) => HOOK.test(readFileSync(f, 'utf8')))
    expect(usesHook.length, 'vacuity floor: the hook must be found across the pages tree')
      .toBeGreaterThan(20)

    const holdouts = files.filter((f) => {
      const src = readFileSync(f, 'utf8')
      if (!src.includes('<EmptyState') || !HOOK.test(src) || src.includes('LoadError')) return false
      const guarded = nodes(src, node => ts.isIfStatement(node) && /!query.data && query.error/.test(node.expression.getText()))
      if (guarded.length) {
        expect(guarded).toHaveLength(1)
        expect(guarded[0].getText()).toContain('Effect status is unavailable.')
        expect(guarded[0].getText()).toContain('onClick={query.refresh}')
        expect(guarded[0].getText()).toContain('return <Section')
        return false
      }
      return true
    }).map((f) => f.slice(dir.length + 1).replace(/\\/g, '/')).sort()

    expect(holdouts).toEqual([
      'triggers/WeekGridView.tsx',
    ])
  })
})
