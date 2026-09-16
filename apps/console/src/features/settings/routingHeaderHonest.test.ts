import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const routing = () => readFileSync(join(SRC, 'features/settings/RoutingPanel.tsx'), 'utf8')

function headerHint(src: string): string {
  return src.match(/<PanelHeader[\s\S]*?hint="([^"]*)"/)?.[1] ?? ''
}

describe('the routing panel describes what it actually does', () => {
  it('reads the real header hint (not vacuously green)', () => {
    const hint = headerHint(routing())
    expect(hint.length, 'the PanelHeader hint must be found').toBeGreaterThan(80)
    expect(hint).toMatch(/per-model efficiency/)
  })

  it('the hint no longer claims the surface cannot change routing', () => {
    const hint = headerHint(routing())
    expect(/Observation only/i.test(hint), 'the page ships routing controls — this claim is false').toBe(false)
    expect(/does not change routing/i.test(hint)).toBe(false)
    expect(/a later capability/i.test(hint), 'the capability already shipped (MRT-4)').toBe(false)
  })

  it('the hint points at the policy section that does the deciding', () => {
    expect(headerHint(routing()), 'a reader should be told where the decision is made').toMatch(/Routing policy/)
  })

  it('the file-level doc comment does not contradict it either', () => {
    const doc = routing().slice(0, routing().indexOf('export function RoutingPanel'))
    expect(/ONLY visualizes/.test(doc), 'the doc comment carried the same stale claim').toBe(false)
    expect(/never changes routing/.test(doc)).toBe(false)
    expect(doc, 'and it should name both halves').toMatch(/DECIDES/)
  })

  it('the policy controls really do write — the reason the old copy was false', () => {
    const src = routing()
    expect(src, 'the section must be declared').toMatch(/function RoutingPolicySection\(/)
    expect(src, 'and actually rendered by the panel').toMatch(/<RoutingPolicySection\s/)
    expect(src, 'and it must persist through the API').toMatch(/api\.setRoutingPolicy/)
    for (const lever of ['mode', 'pin', 'order']) {
      expect(src, `the ${lever} lever must still be written`).toMatch(new RegExp(`save\\(\\{[^}]*${lever}`))
    }
  })
})
