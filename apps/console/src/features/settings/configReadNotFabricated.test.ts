import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) =>
  read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const PANELS = [
  'features/settings/ChatPanel.tsx',
  'features/settings/DurabilityPanel.tsx',
  'features/settings/PacksPanel.tsx',
  'features/settings/AgentDefaultsPanel.tsx',
]

describe('a config panel does not present fabricated values as saved state', () => {
  for (const rel of PANELS) {
    it(`${rel.split('/').pop()} lets the config rejection reach the hook`, () => {
      const code = codeOf(rel)
      expect(code, 'the config read must be bare').toMatch(/api\.gideonConfig\(\)/)
      const chain = code.split('\n').find((l) => l.includes('api.gideonConfig()')) ?? ''
      expect(chain, 'a `.catch` chained onto THIS read fabricates the whole panel')
        .not.toMatch(/\.catch\(\(\)\s*=>/)
    })

    it(`${rel.split('/').pop()} shows the failure instead of the form`, () => {
      const code = codeOf(rel)
      expect(code).toMatch(/<LoadError what="settings" error=\{loadErr\} onRetry=\{refresh\} \/>/)
      const errAt = code.search(/<LoadError\b/)
      const skelAt = code.search(/<FormSkeleton\b/)
      expect(errAt, 'the error branch must come first or it never runs').toBeLessThan(skelAt)
    })
  }

  it('the decorating reads KEEP their fallbacks — this is not a no-catch sweep', () => {
    expect(codeOf('features/settings/ChatPanel.tsx')).toMatch(/api\.dashboardConfig\(\)\.catch\(\(\) => null\)/)
    const dur = codeOf('features/settings/DurabilityPanel.tsx')
    expect(dur).toMatch(/api\.durabilityStatus\(\)\.catch\(\(\) => null\)/)
    expect(dur).toMatch(/api\.durabilityArchive\(\)\.catch\(\(\) => null\)/)
  })

  it('the hub stops poisoning the legibility key it shares with that panel', () => {
    const widgets = codeOf('features/settings/settingsWidgets.tsx')
    const at = widgets.indexOf("'settings:legibility'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    expect(widgets.slice(at, at + 220), 'a substitute here defeats the panel on the hub journey')
      .not.toMatch(/\.catch\(\(\)\s*=>/)
  })

  it('the legibility tile says it failed, like the other four', () => {
    const widgets = codeOf('features/settings/settingsWidgets.tsx')
    const at = widgets.indexOf('title="Legibility"')
    const body = widgets.slice(at, at + 900)
    expect(body).toMatch(/loading=\{c === undefined && !legErr\}/)
    expect(body).toMatch(/Boolean\(legErr\) && <div data-type="caption" className="text-on-surface-low">Couldn&rsquo;t load/)
  })

  it('🔴 the hub stops poisoning the agent-defaults key it shares with that panel', () => {
    const widgets = codeOf('features/settings/settingsWidgets.tsx')
    const at = widgets.indexOf("'settings:agent-defaults'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook, 'found the whole hook').toMatch(/api\.agents\(\)/)
    expect(hook, 'a substitute on the CONFIG read defeats the panel on the hub journey')
      .not.toMatch(/gideonConfig\(\)[^\n]*\.catch\(/)
    expect(hook, 'the default-agent name keeps its fallback').toMatch(/api\.agents\(\)\.then\(\(a\) => a\.default_agent\)\.catch\(\(\) => ''\)/)
  })

  it('🔴 the agent-defaults tile substitutes the TRUE default, not the safe-looking one', () => {
    const widgets = codeOf('features/settings/settingsWidgets.tsx')
    const at = widgets.indexOf('title="Agent defaults"')
    expect(at, 'found the tile').toBeGreaterThan(-1)
    const body = widgets.slice(at - 1400, at + 1200)
    expect(body, 'the honest fallback').toMatch(/approval_mode \?\? 'auto'/)
    expect(body, 'the safe-looking lie is gone').not.toMatch(/approval_mode \?\? 'interactive'/)
  })

  it("VACUITY: the backend default really is `auto`, so 'auto' is the truthful fallback", () => {
    const { readFileSync } = require('node:fs') as typeof import('node:fs')
    const { join } = require('node:path') as typeof import('node:path')
    const loader = readFileSync(join(process.cwd(), "../../runtime/gideon/core/config/loader.py"), 'utf8')
    const cls = loader.match(/class AgentConfig:[\s\S]*?\n\n/)?.[0] ?? ''
    expect(cls, 'found AgentConfig').not.toBe('')
    expect(cls, 'approval_mode defaults to auto').toMatch(/approval_mode[\s\S]{0,120}?default="auto"/)
  })

  it('the census is reproducible, and the rest of the population is stated not swept', () => {
    const files = ['ChatPanel', 'DurabilityPanel', 'PacksPanel', 'AgentDefaultsPanel', 'settingsWidgets']
    for (const f of files) expect(read(`pages/settings/${f}.tsx`).length, `${f} must be readable`).toBeGreaterThan(500)
    const stillSubstituting = files
      .map((f) => (codeOf(`pages/settings/${f}.tsx`).match(/\.catch\(\(\)\s*=>\s*(\[\]|null|undefined|\{\}|\(\{\}|'')/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    expect(stillSubstituting, 'the decorating fallbacks in these five files, measured')
      .toBeGreaterThanOrEqual(33)
  })
})
