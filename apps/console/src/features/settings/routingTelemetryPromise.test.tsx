import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const PY = join(__dirname, "../../../../../runtime/gideon")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const ui = strip(readFileSync(join(SRC, 'features/settings/RoutingPanel.tsx'), 'utf8'))
const bridge = readFileSync(join(PY, 'extensions/providers/provider_bridge.py'), 'utf8')

describe('the routing empty state tells the truth per axis', () => {
  it('the measured list matches the axes the backend actually guards', () => {
    expect(ui).toMatch(
      /const MEASURED_USE_CASES = \['reasoning', 'background', 'loops', 'orchestration'\] as const/,
    )
    expect(bridge, "the bridge's own gate, verbatim").toMatch(
      /if use_case in \("reasoning", "background", "loops", "orchestration"\):\s*\n\s*kwargs\["_guard_use_case"\] = use_case/,
    )
  })

  it('an unmeasured axis says so instead of promising', () => {
    expect(ui, 'the honest branch exists').toContain('Nothing is measured for this axis.')
    expect(ui, 'and it is chosen by membership, not hardcoded per tab').toMatch(
      /\(MEASURED_USE_CASES as readonly string\[\]\)\.includes\(useCase\)/,
    )
    expect(ui).toContain('it fills in as models handle this kind of request')
  })

  it('the default tab is one of the unmeasured ones — which is why this mattered', () => {
    expect(ui).toMatch(/const USE_CASES = \[\s*\{ key: 'chat'/)
    expect(ui).toMatch(/const DEFAULT_USE_CASE = USE_CASES\[0\]\.key/)
    expect(['reasoning', 'background', 'loops', 'orchestration']).not.toContain('chat')
  })

  it('the fold really is inside the guard — the other end of the chain', () => {
    const guard = readFileSync(join(PY, 'security/guardrails/model_call.py'), 'utf8')
    const audit = guard.slice(guard.indexOf('    def _audit('))
    expect(audit.slice(0, 2600), 'the stats fold hangs off the audit').toMatch(
      /record_routing_stats\(_asdict_row\(rec\), home=config_dir\(\)/,
    )
    const gate = bridge.indexOf('if guard_use_case:')
    const wrap = bridge.indexOf('wrap_model_call_guard(', gate)
    expect(gate, 'the gate must be found').toBeGreaterThan(0)
    expect(wrap, 'the wrap happens inside the gate').toBeGreaterThan(gate)
    expect(bridge.slice(0, gate), 'and nowhere before it').not.toMatch(/wrap_model_call_guard\([^)]/)
  })
})
