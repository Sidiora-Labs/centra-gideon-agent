import { describe, it, expect, vi, beforeEach } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Plus, Brain } from 'lucide-react'
import { EmptyState } from '../shared/ui/ListScaffold'
import { workflowPresets } from './workflows/workflowPresets'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

function actionExprs(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/\baction=\{/g)) {
    const start = (m.index ?? 0) + m[0].length
    let i = start
    let depth = 1
    while (i < src.length && depth > 0) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      i++
    }
    out.push(src.slice(start, i - 1))
  }
  return out
}

const SURFACES: { name: string; file: string; primitive: 'EmptyState' | 'PresetEmptyState' }[] = [
  { name: 'Loops', file: 'features/loops/LoopsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Workflows', file: 'features/workflows/WorkflowsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Knowledge', file: 'features/knowledge/KnowledgeListPage.tsx', primitive: 'EmptyState' },
  { name: 'Memory', file: 'features/settings/MemoryPanel.tsx', primitive: 'EmptyState' },
  { name: 'Skills', file: 'features/skills/SkillsPage.tsx', primitive: 'EmptyState' },
  { name: 'Tasks', file: 'features/tasks/TasksListPage.tsx', primitive: 'EmptyState' },
  { name: 'Triggers', file: 'features/triggers/TriggersListPage.tsx', primitive: 'PresetEmptyState' },
]

describe('exactly one general empty-state primitive', () => {
  it('does NOT ship a second one at ui/EmptyState.tsx', () => {
    expect(
      existsSync(join(SRC, 'shared/ui/EmptyState.tsx')),
      'EmptyState lives in the list kit (ui/ListScaffold.tsx); a second file beside it would be two components for one condition',
    ).toBe(false)
  })

  it('exports it from the list kit, with its siblings', () => {
    const kit = read('shared/ui/ListScaffold.tsx')
    expect(kit).toMatch(/export function EmptyState\b/)
    expect(kit).toMatch(/export function LoadError\b/)
    expect(kit).toMatch(/export function ListSkeleton\b/)
  })

  it('keeps the preset on-ramp a documented SIBLING, not a rival', () => {
    const doc = read('shared/ui/PresetEmptyState.doc.ts')
    expect(doc, 'the on-ramp must state how it differs from EmptyState').toMatch(
      /Distinct from EmptyState/,
    )
  })

  it('documents the primitive where uiDocs.drift looks', () => {
    const doc = read('shared/ui/ListScaffold.doc.ts')
    expect(doc).toMatch(/name: 'EmptyState'/)
  })
})

describe('the seven OU-6 surfaces route their empty case through the primitive', () => {
  for (const { name, file, primitive } of SURFACES) {
    it(`${name} (${file}) renders <${primitive}>`, () => {
      const src = read(file)
      expect(src, `${name} must import the shared primitive from the kit`).toMatch(
        primitive === 'EmptyState'
          ? /import \{[^}]*\bEmptyState\b[^}]*\} from '[^']*ListScaffold'/
          : /import \{[^}]*\bPresetEmptyState\b[^}]*\} from '[^']*PresetEmptyState'/,
      )
      expect(src, `${name} must actually render it`).toMatch(new RegExp(`<${primitive}\\b`))
    })

    it(`${name} offers a working action on the genuinely-empty case`, () => {
      const src = read(file)
      if (primitive === 'PresetEmptyState') {
        expect(src, 'the preset grid must be fed a catalog and hand picks back').toMatch(/presets=\{/)
        expect(src).toMatch(/onPick=\{/)
      } else {
        const wired = actionExprs(src).filter((e) => /onClick/.test(e))
        expect(
          wired.length,
          `the empty state on ${name} must offer a next step, not just state a fact`,
        ).toBeGreaterThan(0)
      }
    })
  }

  it('scans the real files (not vacuously green)', () => {
    expect(SURFACES).toHaveLength(7)
    for (const { file } of SURFACES) {
      expect(existsSync(join(SRC, file)), `${file} must exist to be scanned`).toBe(true)
      expect(read(file).length, `${file} must be non-trivial`).toBeGreaterThan(500)
    }
  })
})

const PEP2_CENSUS: {
  surface: string
  file: string
  verdict: 'on-ramp' | 'produced' | 'derived' | 'degenerate'
  why: string
}[] = [
  { surface: 'Workflows › Runs', file: 'features/workflows/WorkflowsListPage.tsx', verdict: 'on-ramp',
    why: 'Runs is the DEFAULT tab, so this is the newcomer\'s first view of Workflows. Its one CTA went to the definitions LIST — twenty-odd machine names. Now a PresetEmptyState of bundled-template cards that seed the existing start() flow, with browse kept as the footer.' },
  { surface: 'Knowledge › Intents', file: 'features/knowledge/KnowledgeListPage.tsx', verdict: 'on-ramp',
    why: 'Named the "New intent" control in prose and left the user to find it in the top bar. Now carries it, sharing blankIntent() with the header so there is one create seed — and its loader stopped swallowing, because a CTA over a failed read is the worse bug.' },
  { surface: 'Artifacts', file: 'features/artifacts/ArtifactGrid.tsx', verdict: 'on-ramp',
    why: 'Hint named the Files page ("save a file as an artifact") with no way to get there. Now a Browse-files action into that existing flow; the prop is required so a call site cannot ship the fact without the way in.' },
  { surface: 'Tasks', file: 'features/tasks/TasksListPage.tsx', verdict: 'on-ramp',
    why: 'New task → onCreate. PEP-2\'s scope asked for TEMPLATE cards here; there is no task-template catalog in the backend to source them from (runtime/gideon/engine/tasks/ ships models+handlers, no templates), and authoring card copy would be the drift the scope forbids. Nothing changed.' },
  { surface: 'Loops', file: 'features/loops/LoopsListPage.tsx', verdict: 'on-ramp', why: '"Start a loop" — the same handler the header CTA uses.' },
  { surface: 'Knowledge › Library', file: 'features/knowledge/KnowledgeListPage.tsx', verdict: 'on-ramp', why: '"Add knowledge" — onCreate, the shared create route.' },
  { surface: 'Knowledge › Decisions', file: 'features/knowledge/DecisionJournal.tsx', verdict: 'on-ramp',
    why: '"Open chat" — the ONLY surface that can create a decision. Logging one also mints its one-shot review trigger, so handlers/knowledge.py deliberately refuses to create a `decision` from the library create picker: an item authored there would be a decision that never comes back. Naming chat in prose and leaving the user to find it is the exact defect this census flagged on Knowledge › Intents, so `onOpenChat` is a REQUIRED prop threaded from KnowledgeSection (the ArtifactGrid rule — a call site cannot ship the fact without the way in).' },
  { surface: 'Memory', file: 'features/settings/MemoryPanel.tsx', verdict: 'on-ramp', why: '"Add a fact" — opens the memory editor in place.' },
  { surface: 'Skills', file: 'features/skills/SkillsPage.tsx', verdict: 'on-ramp', why: 'Browse skills — conditional on `!q`, the shape this file\'s actionExprs() comment defends.' },
  { surface: 'Agents', file: 'features/agents/AgentsListPage.tsx', verdict: 'on-ramp', why: '"New agent" — onCreate, the same flow as the header.' },
  { surface: 'Projects', file: 'features/projects/ProjectsSection.tsx', verdict: 'on-ramp', why: '"New project" — onCreate, the same flow as the header.' },
  { surface: 'Prompts', file: 'features/prompts/PromptsListPage.tsx', verdict: 'on-ramp', why: '"New prompt" — onCreate, the same flow as the header.' },
  { surface: 'Chat', file: 'features/ChatPage.tsx', verdict: 'on-ramp', why: '"New chat" — the same route the composer opens.' },
  { surface: 'Code', file: 'features/code/CodeSection.tsx', verdict: 'on-ramp', why: '"New code project" — the existing clone/open flow.' },
  { surface: 'Apps', file: 'features/apps/AppsSection.tsx', verdict: 'on-ramp', why: '"Browse the Store" — switches to the Store tab, the install flow.' },
  { surface: 'App host › not installed', file: 'features/apps/AppHostPage.tsx', verdict: 'on-ramp',
    why: 'Not a zero-items state but a 404 on #/app/<name>: the app is not installed, and the fix IS the install flow — "Open the Store" deep-links to #/apps?view=store, the same flow the Apps entry above names. The transient-failure sibling renders LoadError, not this.' },
  { surface: 'Terminal', file: 'features/terminal/TerminalPage.tsx', verdict: 'on-ramp', why: '"New session" — spawns a shell, the only way one exists.' },
  { surface: 'Watched sources', file: 'features/knowledge/SourcesPage.tsx', verdict: 'on-ramp', why: '"Add a source" — the existing SourceCreatePage flow.' },
  { surface: 'Scheduled reports', file: 'features/knowledge/ReportsPage.tsx', verdict: 'on-ramp', why: '"New report" — the inline create form on the same page.' },
  { surface: 'Knowledge › Graph', file: 'features/knowledge/KnowledgeGraph.tsx', verdict: 'on-ramp', why: 'Extraction is a per-item action, and the empty state offers it.' },
  { surface: 'Discover', file: 'features/discover/DiscoverPage.tsx', verdict: 'on-ramp', why: 'Open Settings — the off case is a SETTING, and the action reaches it.' },
  { surface: 'Devices', file: 'features/settings/DevicesPanel.tsx', verdict: 'on-ramp',
    why: '"Pair your first device" — the same startPairing() the section above calls, so there is one pairing flow with two entrances. The label deliberately differs from that section\'s "Pair a device" button: identical accessible names on one screen make the action ambiguous to name-based navigation. A paired device is user-created, so "produced" would be dishonest here.' },
  { surface: 'Sender trust', file: 'features/settings/SenderTrustPanel.tsx', verdict: 'produced',
    why: 'A channel appears once someone messages it; a TRUSTED sender appears when they redeem an 8-digit pairing code or the owner clicks Allow on an unknown-sender notification. Neither is authorable from this page, and that is the design, not an omission: EA-7 shipped the READ/REVOKE half deliberately, because granting inbound access should cost a deliberate act (a CLI `gideon pair <channel>`, or an Allow on a notification that names who is asking) rather than being a text field on a settings page. Zero trusted senders is also the SAFE state — the default DM policy is `pairing`, so an empty list means no stranger can reach the agent. An "on-ramp" verdict would be dishonest twice over: there is no mint-a-code API for a button to call, and naming the CLI in prose is exactly the defect this census flagged on Knowledge › Intents.' },
  { surface: 'Sender trust › per channel', file: 'features/settings/SenderTrustPanel.tsx', verdict: 'produced',
    why: 'The inner "Nobody is trusted on <channel>" branch, classified separately because it answers a different question from the outer one: the channel EXISTS and has a posture, and no one has been let in yet. It states that posture ("Strangers must redeem a pairing code") rather than offering an action, for the same reason as the row above.' },
  { surface: 'Inbox', file: 'features/inbox/InboxPage.tsx', verdict: 'produced',
    why: 'Inbox zero is the good news, and items arrive from connected providers rather than being authored — so "produced" still holds and there is still no create button. AMENDED 2026-09-07: this row previously read "the connect flow is Settings › Inbox, NOT a create button on an empty queue", and that reasoning was right about the create button but was applied to one state too many. It reasoned only about the CONNECTED empty queue. On a fresh install `disabled` is true, and there "Inbox zero" is not good news — it is a congratulation for a triage state the user has never been in, printed above a hint telling them to enable a source. The title now branches on `disabled` alongside the hint, and that ONE branch carries an action to the destination this row itself names. It is a setup on-ramp, not a create flow: it authors no inbox item, which is why the verdict stays "produced" rather than becoming "on-ramp". The success branch and the no-match branch still deliberately carry no action, because manufacturing a CTA out of good news is exactly what this taxonomy exists to prevent.' },
  { surface: 'Notifications', file: 'features/notifications/NotificationsPage.tsx', verdict: 'produced', why: 'Emitted by the system. Zero is success.' },
  { surface: 'Companion', file: 'features/companion/CompanionPage.tsx', verdict: 'produced', why: '"Nothing waiting on you" is success.' },
  { surface: 'Learning', file: 'features/learning/LearningPage.tsx', verdict: 'produced', why: '"Nothing to review" is success; the queue fills from captured signals.' },
  { surface: 'Skill proposals', file: 'features/skills/SkillProposals.tsx', verdict: 'produced', why: 'The agent proposes these. A user cannot author a proposal to itself.' },
  { surface: 'Inbox › Proposals', file: 'features/inbox/ProposalsLens.tsx', verdict: 'produced', why: 'Same: agent-authored.' },
  { surface: 'Knowledge › Conflicts', file: 'features/knowledge/ConflictPanel.tsx', verdict: 'produced', why: '"No contradictions recorded" is the outcome a user wants.' },
  { surface: 'Knowledge › Tags', file: 'features/knowledge/TagManager.tsx', verdict: 'produced',
    why: 'A tag comes into existence by tagging a saved item; there is no create-tag flow to link to, and the hint teaches the mechanism. Only reachable when the library is NON-empty — the shared "Knowledge base is empty" state preempts every view.' },
  { surface: 'Memory › Digests', file: 'features/settings/MemoryPanel.tsx', verdict: 'produced', why: 'Digests are generated on a cadence.' },
  { surface: 'Tasks › Graph', file: 'features/tasks/TaskGraph.tsx', verdict: 'derived', why: 'A view of tasks. The create flow lives on the list, one segmented control away.' },
  { surface: 'Companion › sections', file: 'features/companion/CompanionSections.tsx', verdict: 'derived',
    why: 'Four projections (running loops, open tasks, pending inbox, recent notifications) of collections owned by #/loops, #/tasks, #/inbox and #/notifications. A phone triages what already exists; the create flows belong to those surfaces, and the page footer links out to them rather than growing four CTAs that would each be a second entrance to someone else\'s flow.' },
  { surface: 'Files', file: 'features/files/FilesSection.tsx', verdict: 'derived', why: '"No file open" is a selection state, not an empty collection.' },
  { surface: 'Automations › Week', file: 'features/triggers/WeekGridView.tsx', verdict: 'derived',
    why: 'A projection of the schedules owned by the sibling List view, one `Segmented` control away — the same shape as Tasks › Graph. Its hint already teaches why the grid can be empty while triggers exist (only enabled INTERVAL schedules are plotted; a cron expression is not projected yet; a disabled one has no fires), so a CTA here would offer to create a trigger in the one place that cannot show whether the new one will appear.' },
  { surface: 'Tools', file: 'features/tools/ToolsPage.tsx', verdict: 'degenerate',
    why: 'Built-in action tools always exist, so a successful index read cannot be empty — and the failed read already branches to LoadError (the swallow was removed earlier). When importable MCP servers exist, ImportSuggestions is the on-ramp.' },
  { surface: 'Knowledge › Add source', file: 'features/knowledge/SourceCreatePage.tsx', verdict: 'degenerate', why: '"No source kinds are available" means the backend registered no providers.' },
  { surface: 'Settings › Secrets', file: 'features/settings/SecretsPanel.tsx', verdict: 'on-ramp',
    why: '"Add your first secret" focuses the add form\'s name field. This collection\'s create surface IS that form, already on the page, so the on-ramp is a focus rather than a navigation — there is nowhere to navigate to. The hint sentence is the SERVER\'s (`empty_hint`), so the CLI and the dashboard cannot drift on what an empty vault means.' },
]

describe('PEP-2 · every list surface\'s genuinely-empty branch is classified', () => {
  it('classifies every file that renders an empty state (no surface goes unswept)', () => {
    const rendering = new Set<string>()
    const walk = (dir: string) => {
      for (const entry of readdirSync(join(SRC, dir), { withFileTypes: true })) {
        const rel = `${dir}/${entry.name}`
        if (entry.isDirectory()) walk(rel)
        else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) {
          if (/<(Preset)?EmptyState\b/.test(read(rel))) rendering.add(rel)
        }
      }
    }
    walk('features')
    expect(rendering.size, 'files rendering an empty state').toBeGreaterThanOrEqual(28)
    const classified = new Set(PEP2_CENSUS.map((r) => r.file))
    const offLimits = new Set(['features/triggers/TriggersListPage.tsx'])
    const unswept = [...rendering].filter((f) => !classified.has(f) && !offLimits.has(f))
    expect(unswept, 'every empty-state file needs a PEP-2 verdict').toEqual([])
  })

  it('every "on-ramp" verdict is backed by a real action in the file', () => {
    for (const row of PEP2_CENSUS.filter((r) => r.verdict === 'on-ramp')) {
      const src = read(row.file)
      const wired = actionExprs(src).some((e) => /onClick/.test(e))
      const grid = /<PresetEmptyState\b/.test(src) && /presets=\{/.test(src) && /onPick=\{/.test(src)
      expect(wired || grid, `${row.surface} (${row.file}) claims an on-ramp but offers no action`).toBe(true)
    }
  })

  it('records a reason for every non-defect, and no verdict is a bare label', () => {
    for (const row of PEP2_CENSUS) {
      expect(row.why.length, `${row.surface} needs a stated reason`).toBeGreaterThan(15)
      expect(existsSync(join(SRC, row.file)), `${row.file} must exist`).toBe(true)
    }
    for (const v of ['on-ramp', 'produced', 'derived', 'degenerate'] as const) {
      expect(PEP2_CENSUS.filter((r) => r.verdict === v).length, `no ${v} rows`).toBeGreaterThan(0)
    }
  })
})

describe('PEP-2 · the three surfaces this atom changed', () => {
  it('Workflows sources its preset cards from the bundled templates, not from new copy', () => {
    const cat = read('features/workflows/workflowPresets.ts')
    expect(cat, 'summary is the template name, from data').toMatch(/summary:\s*def\.name/)
    expect(cat, 'description is the template\'s own, from data').toMatch(/description:\s*def\.description/)
    expect(cat).toMatch(/import \{[^}]*\btemplateForKind\b[^}]*\} from '\.\/containerKey'/)
    expect(cat, 'must not name a template literally').not.toMatch(/'(code|design|general)-project'|'deep-research'|'goal-pursuit-/)
  })

  it('Workflows offers no card for a template this install does not ship', () => {
    const defs = [{ name: 'code-project', description: 'Ship a code change.', source: 's', version: 1, tags: [], provider: 'p' }]
    const presets = workflowPresets(defs)
    expect(presets.map((p) => p.id)).toEqual(['code'])
    expect(presets[0].summary).toBe('code-project')
    expect(presets[0].description).toBe('Ship a code change.')
    expect(presets[0].prefill).toBe('code-project')
    expect(workflowPresets([])).toEqual([])
  })

  it('Workflows keeps a non-preset branch for the no-templates install', () => {
    const src = read('features/workflows/WorkflowsListPage.tsx')
    expect(src, 'the preset grid must be gated on there BEING presets').toMatch(/presets\.length > 0/)
    expect(src, 'and the pre-PEP-2 empty state must survive as the fallback').toMatch(/title="No workflow runs yet"[\s\S]{0,400}?action=\{\{ label: 'Browse definitions'/)
  })

  it('Knowledge intents share ONE create seed between the header and the empty state', () => {
    const src = read('features/knowledge/KnowledgeListPage.tsx')
    expect(src).toMatch(/function blankIntent\(\): KnowledgeIntent/)
    expect((src.match(/blankIntent\(\)\)/g) ?? []).length, 'header + empty state').toBe(2)
    expect(src, 'the ?intent=__new__ deep-link uses the same seed').toMatch(
      /intentTok === '__new__'\s*\n\s*\? blankIntent\(\)/,
    )
    expect((src.match(/\{ id: '', goal: '',/g) ?? []).length, 'the blank shape has one definition').toBe(1)
  })

  it('Knowledge intents branch on the failed read BEFORE the empty state', () => {
    const src = read('features/knowledge/KnowledgeListPage.tsx')
    expect(src, 'the rejection must be captured').toMatch(/catch\(\(e\) => \{ setIntentsErr\(e\)/)
    expect(src, 'no swallow may remain on the intents read').not.toMatch(/knowledgeIntents\(\)[\s\S]{0,120}catch\(\(\) =>/)
    const errAt = src.indexOf('if (intentsErr) return <LoadError what="intents"')
    const emptyAt = src.indexOf('title="No intents yet"')
    expect(errAt, 'the intents error branch must exist').toBeGreaterThan(-1)
    expect(emptyAt).toBeGreaterThan(-1)
    expect(errAt, 'the error branch must precede the empty one').toBeLessThan(emptyAt)
  })

  it('Artifacts cannot ship the fact without the way in', () => {
    const src = read('features/artifacts/ArtifactGrid.tsx')
    expect(src).toMatch(/onBrowseFiles: \(\) => void/)
    expect(src).not.toMatch(/onBrowseFiles\?:/)
    expect(read('features/artifacts/ArtifactsSection.tsx'), 'the only call site wires it to Files').toMatch(
      /onBrowseFiles=\{\(\) => navigate\('files'\)\}/,
    )
  })
})

describe('PEP-2 · the expert path is unchanged when the list is not empty', () => {
  const defs = [
    { name: 'code-project', description: 'Ship a code change.', source: 'bundled', version: 1, tags: [], provider: 'core' },
    { name: 'deep-research', description: 'Investigate a topic.', source: 'bundled', version: 1, tags: [], provider: 'core' },
  ]
  const run = {
    id: 'r1', workflow_name: 'code-project', status: 'running', started_at: 0, elapsed_seconds: 3,
  }

  async function renderWorkflows(runs: unknown[]) {
    vi.doMock('../shared/data/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        workflowDefs: () => Promise.resolve({ defs, total: defs.length }),
        workflowRuns: () => Promise.resolve({ runs, total: runs.length }),
        workflowSurfacing: () => Promise.reject(new Error('not needed')),
      },
    }))
    const { WorkflowsListPage } = await import('./workflows/WorkflowsListPage')
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)
  }

  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  it('EMPTY: the preset cards are there and each names its bundled template', async () => {
    await renderWorkflows([])
    expect(await screen.findByRole('heading', { name: 'No workflow runs yet' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Work on code — code-project/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Research a topic — deep-research/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Browse all definitions/ })).toBeInTheDocument()
  })

  it('NON-EMPTY: the run renders and not one preset card appears', async () => {
    await renderWorkflows([run])
    expect(await screen.findByRole('button', { name: 'code-project — run r1' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No workflow runs yet' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Work on code/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Research a topic/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Browse all definitions/ })).toBeNull()
  })
})

describe('an empty state is a launchpad, not a dead end', () => {
  it('names itself with a heading, so the surface is not silently blank', () => {
    render(<EmptyState icon={Brain} title="No memories yet" hint="Add one, or let a chat record them." />)
    expect(screen.getByRole('heading', { name: 'No memories yet' })).toBeInTheDocument()
    expect(screen.getByText('Add one, or let a chat record them.')).toBeInTheDocument()
  })

  it('exposes the action as a real keyboard-reachable button', async () => {
    const onClick = vi.fn()
    render(<EmptyState icon={Brain} title="No memories yet" action={{ label: 'Add a fact', onClick, icon: Plus }} />)
    const btn = screen.getByRole('button', { name: /Add a fact/ })
    await userEvent.tab()
    expect(btn).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not opt its action out of the app-wide focus ring', () => {
    const ring = read('shared/theme/tokens.css')
    expect(ring, 'the app-wide keyboard ring must exist').toMatch(
      /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-primary\)/,
    )
    render(<EmptyState title="t" action={{ label: 'Go', onClick: () => {} }} />)
    const cls = (screen.getByRole('button', { name: 'Go' }).className || '').split(/\s+/)
    const optsOut = cls.some((c) => c === 'outline-none' || c === 'focus:outline-none')
    const replaces = cls.some((c) => c.startsWith('focus-visible:ring') || c.startsWith('focus-visible:outline'))
    expect(optsOut && !replaces, 'the CTA suppressed the global ring without replacing it').toBe(false)
  })

  it('is NOT a live region — "you have none" is a normal answer', () => {
    const { container } = render(<EmptyState title="No loops yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  it('renders no button at all when there is no next step', () => {
    const { container } = render(<EmptyState title="No matching loops" hint="Try another filter." />)
    expect(container.querySelector('button')).toBeNull()
  })
})
