import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PageTitle } from './PageTitle'


describe('PageTitle', () => {
  it('renders an h1', () => {
    render(<PageTitle>Tasks</PageTitle>)
    expect(screen.getByRole('heading', { level: 1, name: 'Tasks' })).toBeTruthy()
  })

  it('carries the title-l type role, not a per-component size', () => {
    render(<PageTitle>Tasks</PageTitle>)
    expect(screen.getByRole('heading', { level: 1 }).getAttribute('data-type')).toBe('title-l')
  })

  it('keeps the ink token and merges extra classes', () => {
    render(<PageTitle className="flex items-center gap-s">Inbox</PageTitle>)
    const h = screen.getByRole('heading', { level: 1 })
    expect(h.className).toContain('text-on-surface')
    expect(h.className).toContain('flex items-center gap-s')
  })

  it('lets the title own trailing chrome (a count badge reads as part of the name)', () => {
    render(<PageTitle>Notifications <span>3</span></PageTitle>)
    expect(screen.getByRole('heading', { level: 1, name: 'Notifications 3' })).toBeTruthy()
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.doc\.tsx$/.test(n) ? [p] : []
  })

const DESTINATIONS = [
  'features/tasks/TasksListPage.tsx',
  'features/tools/ToolsPage.tsx',
  'features/triggers/TriggersListPage.tsx',
  'features/terminal/TerminalPage.tsx',
  'features/agents/AgentsListPage.tsx',
  'features/knowledge/KnowledgeListPage.tsx',
  'features/prompts/PromptsListPage.tsx',
  'features/loops/LoopsListPage.tsx',
  'features/settings/SettingsPage.tsx',
  'features/inbox/InboxPage.tsx',
  'features/notifications/NotificationsPage.tsx',
  'features/projects/ProjectsSection.tsx',
  'features/skills/SkillsPage.tsx',
  'features/learning/LearningPage.tsx',
  'features/artifacts/ArtifactsSection.tsx',
  'features/apps/AppsSection.tsx',
  'features/files/FilesSection.tsx',
  'features/discover/DiscoverPage.tsx',
  'features/workflows/WorkflowsListPage.tsx',
  'shared/ui/ListScaffold.tsx',
  'features/workflows/WorkflowRunDetail.tsx',
  'features/workflows/WorkflowDefDetail.tsx',
  'features/apps/AppFrame.tsx',
  'features/prompts/PromptViewPage.tsx',
  'features/tasks/TaskCreatePage.tsx',
  'features/triggers/TriggerCreatePage.tsx',
  'features/agents/AgentCreatePage.tsx',
  'features/prompts/PromptCreatePage.tsx',
  'features/knowledge/KnowledgeCreatePage.tsx',
]

describe('every converged destination names itself', () => {
  for (const rel of DESTINATIONS) {
    it(`${rel} titles itself with PageTitle`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must render the primitive').toMatch(/<PageTitle[\s>]/)
      expect(src, 'must import it').toMatch(/import \{ PageTitle \}/)
    })
  }

  it('none of them kept a bare title-l span in the TopBar left slot', () => {
    const holdouts: string[] = []
    for (const rel of DESTINATIONS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      if (/left=\{<span data-type="title-l"/.test(src)) holdouts.push(rel)
    }
    expect(holdouts, `still hand-rolling the page title:\n  ${holdouts.join('\n  ')}`).toEqual([])
  })

  it('renders exactly ONE PageTitle per destination (a page has one name)', () => {
    const TWO_STEP = ['features/skills/SkillsPage.tsx', 'features/knowledge/KnowledgeCreatePage.tsx',
      'features/projects/ProjectsSection.tsx']
    const offenders: string[] = []
    for (const rel of DESTINATIONS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const n = [...src.matchAll(/<PageTitle[\s>]/g)].length
      if (n > 1 && !TWO_STEP.includes(rel)) offenders.push(`${rel} (${n})`)
    }
    expect(offenders, `more than one page title:\n  ${offenders.join('\n  ')}`).toEqual([])
  })

  it('does NOT give a docked panel header an h1', () => {
    const chat = readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8')
    expect(
      /left=\{<PageTitle[^>]*>Chat history/.test(chat),
      'the Chat history panel header must stay a span — a drawer is not the page',
    ).toBe(false)
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})


describe('an entity is the destination when the URL says so', () => {
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the path-segment detail routes carry the entity as their h1', () => {
    expect(read('features/workflows/WorkflowRunDetail.tsx'), 'the run')
      .toMatch(/<PageTitle className="truncate">\{run\.workflow\}<\/PageTitle>/)
    expect(read('features/workflows/WorkflowDefDetail.tsx'), 'the definition')
      .toMatch(/<PageTitle className="truncate">\{name\}<\/PageTitle>/)
    expect(read('features/projects/ProjectsSection.tsx'), "the project view's own titleNode")
      .toMatch(/<PageTitle className="truncate">\{project\.name\}<\/PageTitle>/)
    expect(read('features/apps/AppFrame.tsx'), 'the installed app')
      .toMatch(/<PageTitle className="flex items-center gap-s">/)
    expect(read('features/knowledge/KnowledgeDetailPage.tsx'), 'the knowledge item')
      .toMatch(/<PageTitle className="truncate min-w-0">/)
  })

  it('a peek does NOT take one — the list is still the destination', () => {
    const knowledge = read('features/knowledge/KnowledgeListPage.tsx')
    expect(knowledge).toMatch(/<PageTitle[\s>]/)
    expect((knowledge.match(/<PageTitle[\s>]/g) ?? []).length, 'one per destination, not one per panel').toBe(1)
    const chat = read('features/ChatPage.tsx')
    expect(/left=\{<PageTitle[^>]*>Chat history/.test(chat), 'the chat history drawer stays a span').toBe(false)
  })

  it('none of the four kept the bare span it replaced', () => {
    for (const rel of ['features/workflows/WorkflowRunDetail.tsx', 'features/workflows/WorkflowDefDetail.tsx',
      'features/apps/AppFrame.tsx', 'features/knowledge/KnowledgeDetailPage.tsx']) {
      expect(read(rel), `${rel} still hand-rolls a title`).not.toMatch(/<span data-type="title-l"/)
    }
    const projects = read('features/projects/ProjectsSection.tsx')
    expect((projects.match(/<span data-type="title-l"/g) ?? []).length, 'only the peek-panel header').toBeLessThanOrEqual(1)
  })
})

describe('no destination skips a heading level', () => {
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')


  it("the task form's section headers are h2, directly under the page h1", () => {
    const src = read('features/tasks/TaskForm.tsx')
    expect(src).toMatch(/<h2 className="text-on-surface text-\[0\.8125rem\]" style=\{fvs\(550\)\}>\{title\}<\/h2>/)
    expect(src, 'no h3 section header left to skip a level').not.toMatch(/<h3/)
  })

  it('nothing in pages/tasks renders an h3 at all', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const offenders = walk(join(SRC, 'features/tasks')).filter((abs) => /<h3[\s>]/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these would skip h1 → h3:\n${offenders.join('\n')}`).toEqual([])
  })
})

describe('the dashboard does not skip a heading level', () => {
  const files = ['features/dashboard/DashboardPage.tsx', 'features/dashboard/PinnedTiles.tsx']

  it.each(files)('%s renders its section header as h2', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, 'section header must be an h2, directly under the page h1').toMatch(/<h2 data-type="label-l"/)
    expect(src, 'no h3 section header left to skip a level').not.toMatch(/<h3 data-type="label-l"/)
  })

  it('finds the shared Section component (not vacuously green)', () => {
    const src = readFileSync(join(SRC, 'features/dashboard/DashboardPage.tsx'), 'utf8')
    expect(src).toMatch(/function Section\(/)
  })
})


describe('a panel that owns its h1 does not skip a level', () => {
  const PAGES = join(process.cwd(), "src/features")
  const walkPages = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkPages(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })
  const strip = (s: string) => s
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

  function ownsItsH1() {
    return walkPages(PAGES)
      .map((abs) => ({ rel: abs.slice(PAGES.length + 1), src: strip(readFileSync(abs, 'utf8')) }))
      .filter((f) => /<h[1-6]\b/.test(f.src) && /<PanelHeader\b|<PageTitle\b/.test(f.src))
  }

  it('the premises hold: PanelHeader is an h1 and Section is an h2', () => {
    const ui = readFileSync(join(process.cwd(), "src/features/settings/settingsUI.tsx"), 'utf8')
    expect(ui, 'PanelHeader must render the panel title as h1').toMatch(/<h1 className="text-on-surface"/)
    expect(ui, 'Section must render its heading as h2').toMatch(/<h2 data-type="title-m" className=\{`mb-s text-on-surface/)
  })

  it('finds the population (not vacuously green)', () => {
    const files = ownsItsH1().map((f) => f.rel)
    expect(files.length, 'the scan must resolve the self-titled panels').toBeGreaterThanOrEqual(3)
    expect(files).toContain('settings/VoicePanel.tsx')
  })

  it('every own heading has the level above it available', () => {
    const bad: string[] = []
    for (const { rel, src } of ownsItsH1()) {
      const available = new Set<number>()
      if (/<PanelHeader\b|<PageTitle\b/.test(src)) available.add(1)
      if (/<Section\b/.test(src)) available.add(2)
      for (const m of src.matchAll(/<h([1-6])\b/g)) {
        const level = Number(m[1])
        if (level > 1 && !available.has(level - 1)) bad.push(`${rel}: h${level} with no h${level - 1} above it`)
        available.add(level)
      }
    }
    expect(bad, `these skip a heading level:\n${bad.join('\n')}`).toEqual([])
  })

  it('the two fixed panels keep what replaced their skipped headings', () => {
    const providers = readFileSync(join(PAGES, 'settings/ProvidersPanel.tsx'), 'utf8')
    const sectionCall = providers.slice(providers.indexOf('<Section'), providers.indexOf('<Section') + 400)
    expect(sectionCall, 'the entity group renders the shared Section').toContain('<Section')
    for (const prop of ['icon={Icon}', 'hint={hint}', '{label}']) {
      expect(sectionCall, `it must still hand Section its ${prop}`).toContain(prop)
    }
    expect(sectionCall, 'with the muted tone — coral on nine decorative glyphs is a system violation')
      .toContain('iconTone="muted"')
    expect(/<h[1-6]\b/.test(providers), 'and writes no heading tag of its own').toBe(false)
    const voice = readFileSync(join(PAGES, 'settings/VoicePanel.tsx'), 'utf8')
    expect(voice, 'Learned corrections nests under its Section h2').toMatch(/<h3 [^>]*>Learned corrections<\/h3>/)
  })
})
