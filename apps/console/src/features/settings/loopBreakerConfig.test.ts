import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(`${process.cwd()}/src/features/settings/GuardrailsPanel.tsx`, 'utf8')

describe('tool-loop settings contract', () => {
  it('separates provider failures from tool-loop failures and declares the floor', () => {
    expect(source).toContain('title="Provider circuit breaker"')
    expect(source).toContain('title="Tool-loop breaker"')
    expect(source).toContain('value={cfg.loop_breaker?.circuit_threshold ?? 30} min={1} step={1}')
    expect(source).toContain("patchNum('loop_breaker.circuit_threshold', v, 'Tool failure ceiling')")
    expect(source).toContain('changes take effect on the next run')
  })

  it('only updates the new persisted value after a successful save', () => {
    expect(source).toMatch(/const saved = await patchNum\('loop_breaker.circuit_threshold'[\s\S]*?if \(saved\) setCfg/)
    const patch = source.slice(source.indexOf('const patchNum'), source.indexOf('  return ('))
    expect(patch).toContain('.then(() => true).catch(')
    expect(patch).toContain("notify(`Couldn't save")
    expect(patch).toContain('return false')
  })

  it('never presents a failed save as saved', () => {
    const row = source.slice(source.indexOf('function NumberRow'))
    expect(row).toMatch(/if \(result === false\) return\s+setSaved\(true\)/)
    expect(row).toContain('.catch(() => setSaved(false))')
    expect(row).toContain('setSaved(false)\n    onSave(n)')
  })
})
