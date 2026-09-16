import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const widget = readFileSync(join(SRC, 'shared/ui/SystemWidget.tsx'), 'utf8')

function runningAgents(): string {
  const at = widget.indexOf('function RunningAgents(')
  expect(at, 'RunningAgents must still exist').toBeGreaterThan(-1)
  const end = widget.indexOf('\nfunction ', at + 1)
  expect(end, 'the component must terminate before the next top-level function').toBeGreaterThan(at)
  return widget
    .slice(at, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the background-agent fleet read', () => {
  it('reads its subject — a rail over nothing asserts nothing', () => {
    expect(widget.length, 'SystemWidget.tsx did not read').toBeGreaterThan(5_000)
    const fn = runningAgents()
    expect(fn, 'it must still load the fleet').toMatch(/api\.spawnedAgents\(\)/)
    expect(fn, 'and still poll while open').toMatch(/setInterval\(load, 4000\)/)
  })

  it('🔴 a failed read does not clobber the list it already had', () => {
    const fn = runningAgents()
    expect(fn, 'a failed read must not overwrite a known-good fleet with []')
      .not.toMatch(/catch\s*\(\s*\)?\s*=>\s*setAgents\(/)
    expect(fn, 'and must not clear it to an empty array anywhere in the failure path')
      .not.toMatch(/catch[\s\S]{0,60}?setAgents\(\s*\[\s*\]\s*\)/)
  })

  it('the failure is its own state, distinguishable from an empty fleet', () => {
    const fn = runningAgents()
    expect(fn, 'a failure flag separate from the list').toMatch(/setFailed\(true\)/)
    expect(fn, 'a good read must clear the failure').toMatch(/setFailed\(false\)/)
  })

  it('a first read that fails renders a notice instead of nothing', () => {
    const fn = runningAgents()
    expect(fn, 'the never-loaded failure case must be handled before the empty-fleet guard')
      .toMatch(/if\s*\(\s*failed\s*&&\s*!agents\s*\)/)
    expect(fn, 'and it must say so in words a user can read')
      .toMatch(/Couldn’t read the background agents/)
    expect(
      fn.indexOf('failed && !agents') < fn.indexOf('agents.length === 0'),
      'the failure branch must precede the empty-fleet guard, or it is unreachable',
    ).toBe(true)
  })

  it('a stale list does not claim to be live', () => {
    expect(runningAgents(), 'the header marks a non-updating list').toMatch(/not updating/)
  })

  it('✅ CONTROL — a genuinely empty fleet is still hidden', () => {
    expect(runningAgents(), 'an empty fleet still renders nothing')
      .toMatch(/if\s*\(!agents\s*\|\|\s*agents\.length === 0\)\s*return null/)
  })

  it('the sibling consumer still keeps its last known value too', () => {
    const hook = readFileSync(join(SRC, 'shared/data/useAgentActivity.ts'), 'utf8')
    expect(hook, 'useAgentActivity also reads /api/spawn').toMatch(/api\.spawnedAgents\(\)/)
    expect(hook, 'and records the error rather than emptying its sources').toMatch(/catch\([\s\S]{0,80}?setError\(/)
    expect(hook, 'it must not blank its sources on failure').not.toMatch(/catch[\s\S]{0,80}?setSources\(\s*(null|\{)/)
  })
})
