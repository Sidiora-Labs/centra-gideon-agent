import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SETTINGS = join(process.cwd(), "src/features/settings")
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const PANELS: Array<[string, string]> = [
  ['AgentDefaultsPanel.tsx', 'agent'],
  ['AmbientPanel.tsx', 'ambient'],
  ['EvalsPanel.tsx', 'evals'],
  ['GuardrailsPanel.tsx', 'guardrails'],
  ['LegibilityPanel.tsx', 'legibility'],
]

describe('a settings panel reports a failed config read', () => {
  it.each(PANELS)('%s does not fabricate an empty %s section', (file, section) => {
    const src = strip(readFileSync(join(SETTINGS, file), 'utf8'))
    expect(src, `${file} must still read its section`).toMatch(
      new RegExp(`gideonConfig\\(\\)[\\s\\S]{0,90}c\\.${section}`),
    )
    expect(/gideonConfig\(\)[\s\S]{0,160}\.catch\(\(\) => \(\{/.test(src),
      `${file}: an empty section is indistinguishable from saved defaults`).toBe(false)
  })

  it.each(PANELS)('%s reads the hook error and replaces the form with it', (file) => {
    const src = strip(readFileSync(join(SETTINGS, file), 'utf8'))
    expect(src, `${file}: the rejection must be read`).toMatch(/error:\s*loadErr/)
    expect(src, `${file}: and reported`).toMatch(/<LoadError what="settings" error=\{loadErr\} onRetry=\{refresh\}/)
    const errAt = src.search(/<LoadError\b/)
    const skelAt = src.search(/<FormSkeleton\b/)
    expect(skelAt, `${file} must still have a loading state`).toBeGreaterThan(-1)
    expect(errAt, `${file}: the error branch must precede the skeleton`).toBeLessThan(skelAt)
  })

  it('reads the real files (not vacuously green)', () => {
    for (const [file] of PANELS) {
      expect(readFileSync(join(SETTINGS, file), 'utf8').length, file).toBeGreaterThan(1500)
    }
  })
})
