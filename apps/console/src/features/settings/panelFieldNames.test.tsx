import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SETTINGS = join(process.cwd(), "src/features/settings")
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')

const code = (f: string) =>
  read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the four panels that had unnamed controls', () => {
  it('SecurityPanel host inputs name themselves from the list they add to', () => {
    expect(code('SecurityPanel.tsx')).toMatch(/aria-label=\{`Add a host to \$\{label\.toLowerCase\(\)\}`\}/)
  })

  it('SecurityPanel denylist input says what typing there DOES', () => {
    expect(code('SecurityPanel.tsx')).toMatch(/aria-label="Add a shell denylist pattern \(regex\)"/)
  })

  it('the shared StrListField input names itself from its Field label', () => {
    expect(code('settingsUI.tsx')).toMatch(/aria-label=\{`Add to \$\{label\.toLowerCase\(\)\}`\}/)
    expect(code('AgentDefaultsPanel.tsx')).not.toMatch(/function StrListField/)
  })

  it('VoicePanel vocabulary input is named', () => {
    expect(code('VoicePanel.tsx')).toMatch(/aria-label="Add a vocabulary term"/)
  })

  it('ProjectionRulesPanel names all six controls, scoped per row', () => {
    const src = code('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/aria-label="Rule name"/)
    expect(src).toMatch(/aria-label="New rule name"/)
    expect(src).toMatch(/aria-label=\{rule\.name \? `Match regex for \$\{rule\.name\}` : 'Match regex'\}/)
    expect(src).toMatch(/aria-label="Match regex for the new rule"/)
    expect(src).toMatch(/aria-label=\{forRule \? `Strategy for \$\{forRule\}` : 'Strategy for the new rule'\}/)
    expect(src).toMatch(/aria-label=\{rule\.name \? `Remove rule \$\{rule\.name\}` : 'Remove rule'\}/)
  })
})

describe('a component that renders more than once must not carry a constant name', () => {
  it('StrategyPicker takes forRule, and both call sites distinguish themselves', () => {
    const src = code('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/forRule\?: string/)
    expect(src).toMatch(/<StrategyPicker[\s\S]*?forRule=\{shown\.name\}/)
  })

  it('HostList renders twice with different labels — which is why the name is derived', () => {
    const src = code('SecurityPanel.tsx')
    const uses = [...src.matchAll(/<HostList\s+label="([^"]+)"/g)].map((m) => m[1])
    expect(uses).toEqual(['Allowed hosts', 'Denied hosts'])
    expect(/aria-label="Add a host"/.test(src), 'a constant here would announce both identically').toBe(false)
  })
})

describe('what a SOURCE rail can and cannot decide here', () => {

  it('no fixed control silently loses its name (the panels driven this cycle)', () => {
    const MUST_KEEP: Array<[string, RegExp]> = [
      ['SecurityPanel.tsx', /aria-label=\{`Add a host to \$\{label\.toLowerCase\(\)\}`\}/],
      ['SecurityPanel.tsx', /aria-label="Add a shell denylist pattern \(regex\)"/],
      ['settingsUI.tsx', /aria-label=\{`Add to \$\{label\.toLowerCase\(\)\}`\}/],
      ['VoicePanel.tsx', /aria-label="Add a vocabulary term"/],
      ['MemoryPanel.tsx', /aria-label="Lesson rule"/],
      ['MemoryPanel.tsx', /aria-label="Fact key"/],
      ['MemoryPanel.tsx', /aria-label="Fact value"/],
      ['ProjectionRulesPanel.tsx', /aria-label="Rule name"/],
      ['ProjectionRulesPanel.tsx', /aria-label="New rule name"/],
      ['ProjectionRulesPanel.tsx', /aria-label="Match regex for the new rule"/],
    ]
    const missing = MUST_KEEP.filter(([f, re]) => !re.test(code(f))).map(([f, re]) => `${f} ${re}`)
    expect(missing, `these names were measured on the live DOM and must not regress:\n  ${missing.join('\n  ')}`).toEqual([])
  })

  it('the check is not vacuous — every named file exists and is scanned', () => {
    for (const f of ['SecurityPanel.tsx', 'AgentDefaultsPanel.tsx', 'settingsUI.tsx', 'VoicePanel.tsx', 'MemoryPanel.tsx', 'ProjectionRulesPanel.tsx']) {
      expect(readdirSync(SETTINGS), `${f} must exist`).toContain(f)
      expect(code(f).length).toBeGreaterThan(200)
    }
  })
})
