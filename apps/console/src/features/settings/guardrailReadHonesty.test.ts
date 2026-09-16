import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/settings/GuardrailsPanel.tsx")
const raw = readFileSync(SRC, 'utf8')
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the guardrails reads state their failure instead of inventing one', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function IncidentSection\(/)
    expect(src).toMatch(/function ProviderHealthSection\(/)
    expect(raw.length).toBeGreaterThan(4000)
  })

  it('the incident read never substitutes { active: false }', () => {
    expect(/catch\(\(\)\s*=>\s*setState\(\{\s*active:\s*false/.test(src),
      'a failed read must not claim the kill switch is off').toBe(false)
    expect(src, 'the rejection must be captured').toMatch(/api\.incident\(\)[\s\S]{0,160}\.catch\(setLoadErr\)/)
  })

  it('the incident hint reports the failure, and the toggle stays disabled', () => {
    expect(src).toMatch(/Couldn't check whether incident mode is active/)
    expect(src).toMatch(/disabled=\{busy \|\| !state\}/)
  })

  it('provider health does not substitute an empty audit', () => {
    expect(/catch\(\(\)\s*=>\s*setRows\(\[\]\)\)/.test(src),
      '"no calls recorded yet" is a claim, not an error').toBe(false)
    expect(src).toMatch(/Couldn't check provider health/)
    expect(src, 'and it announces, because it replaces content the user was reading').toMatch(/role="alert"/)
  })
})
