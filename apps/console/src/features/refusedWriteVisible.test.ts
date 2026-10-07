import { namedOwner } from '../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const code = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const OUTSIDE_THE_SETTINGS_SWEEP = [
  { file: 'features/agents/agentEditorState.ts', write: 'saveAgentMetadata', form: 'inline' as const },
  { file: 'features/dashboard/widgets/TasksWidget.tsx', write: 'updateTask', form: 'toast' as const },
]

describe('a refused write is visible, not merely non-confirmed', () => {
  it('reads its subjects (a rail over nothing asserts nothing)', () => {
    for (const { file, write } of OUTSIDE_THE_SETTINGS_SWEEP) {
      expect(code(file).length, `${file} did not read`).toBeGreaterThan(1_000)
      expect(code(file), `${file} no longer calls api.${write}`).toContain(`api.${write}(`)
    }
  })

  it.each(OUTSIDE_THE_SETTINGS_SWEEP)('$file does not swallow api.$write', ({ file }) => {
    const empty = [...code(file).matchAll(/catch\s*(?:\([^)]*\))?\s*\{\s*\}/g)]
    expect(
      empty.length,
      `an empty catch in ${file} makes a refused write identical to a click that never landed`,
    ).toBe(0)
  })

  const componentBody = (rel: string, name: string) => {
    return namedOwner(code(rel), name)
  }

  const INLINE_REPORTERS: [string, string][] = [
    ['features/settings/MemoryPanel.tsx', 'StudioDocEditor'],
    ['features/agents/AgentDetail.tsx', 'RoutingNotesEditor'],
    ['features/agents/AgentDetail.tsx', 'RoutingStatusView'],
  ]

  it.each(INLINE_REPORTERS)('%s › %s reports its refused write inline', (rel, name) => {
    const body = componentBody(rel, name)
    const slot = name === 'RoutingNotesEditor' ? 'notes.error' : name === 'RoutingStatusView' ? 'operation.error' : 'err'
    expect(body).toContain(`${slot} && <span role="alert"`)
    expect(body).toContain(`{${slot}}</span>`)
    const handler = name === 'StudioDocEditor' ? body : namedOwner(code('features/agents/agentEditorState.ts'), 'useAgentWrite')
    expect(handler).toMatch(/catch \((?:e|failure)\)/)
    expect(handler).toMatch(/(?:setErr|setError)\(/)
    expect(handler).toMatch(/instanceof Error \? (?:e|failure)\.message/)
    if (name === 'RoutingNotesEditor') {
      expect(body).toContain('useAgentRoutingNotes(agentName)')
      const notes = namedOwner(code('features/agents/agentEditorState.ts'), 'useAgentRoutingNotes')
      expect(notes).toContain('useAgentWrite(agentName)')
      expect(notes).toContain('operation.write(() => api.saveAgentMetadata')
    }
    if (name === 'RoutingStatusView') expect(body).toContain('operation.write(() => unmuteAgent')
    expect(body).not.toMatch(/catch\s*\{\s*\}/)
  })

  it('the two save editors still flash their success confirmation', () => {
    for (const name of ['StudioDocEditor', 'RoutingNotesEditor']) {
      const rel = name === 'StudioDocEditor' ? 'features/settings/MemoryPanel.tsx' : 'features/agents/AgentDetail.tsx'
      expect(componentBody(rel, name), `${name} must still flash Saved ✓`).toMatch(/Saved ✓/)
    }
  })

  it('the level pill stays gated on the response, and reports through the .catch form', () => {
    const src = code('features/settings/DiagnosticsPanel.tsx')
    expect(src).toMatch(/\.then\(\(r\) => setLevel\(r\.level\)\)/)
    expect(src).toMatch(/\.catch\(reportActionFailure\(`set the log level to \$\{l\}`\)\)/)
    expect(src, 'setLevel must not run outside that success handler').not.toMatch(
      /await api\.setLogLevel\(l\);\s*setLevel/,
    )
  })

  it('the dashboard tick names WHICH task, and gates both of its success signals', () => {
    const src = code('features/dashboard/widgets/TasksWidget.tsx')
    expect(src, 'the row threads its own title in — the widget is a list').toMatch(
      /complete\(t\.id,\s*t\.title\)/,
    )
    expect(src).toMatch(/reportingWrite\(`complete [^`]*\$\{title\}/)
    expect(src).toMatch(/if \(!ok\) return\s*\n\s*setDone\(/)
    expect(src, 'refreshAll must sit after the gate, not before it').toMatch(
      /setDone\([\s\S]{0,80}?refreshAll\(\)/,
    )
  })
})
