import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const code = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const OUTSIDE_THE_SETTINGS_SWEEP = [
  { file: 'features/agents/AgentDetail.tsx', write: 'saveAgentMetadata', form: 'inline' as const },
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
    const src = code(rel)
    const at = src.indexOf(`function ${name}(`)
    expect(at, `${rel} no longer defines ${name}`).toBeGreaterThan(-1)
    const end = src.indexOf('\n}', at)
    expect(end, `${name}'s body did not terminate`).toBeGreaterThan(at)
    return src.slice(at, end)
  }

  const INLINE_REPORTERS: [string, string][] = [
    ['features/settings/MemoryPanel.tsx', 'StudioDocEditor'],
    ['features/agents/AgentDetail.tsx', 'RoutingNotesEditor'],
    ['features/agents/AgentDetail.tsx', 'RoutingStatusView'],
  ]

  it.each(INLINE_REPORTERS)('%s › %s reports its refused write inline', (rel, name) => {
    const body = componentBody(rel, name)
    expect(
      [...body.matchAll(/\{err && <span role="alert"[^>]*>\{err\}<\/span>\}/g)].length,
      `${name} must report beside the control that was pressed — a toast would put the answer ` +
        'somewhere other than where the user is looking',
    ).toBe(1)
    expect(
      [...body.matchAll(/catch \(e\) \{ setErr\(e instanceof Error \? e\.message : '/g)].length,
      `${name}: the slot needs a handler filling it from the server's own sentence`,
    ).toBe(1)
    expect(body, `${name} must not swallow it instead`).not.toMatch(/catch\s*\{\s*\}/)
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
