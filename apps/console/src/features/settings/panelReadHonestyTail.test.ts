import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SETTINGS = join(process.cwd(), "src/features/settings")
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')
const codeOf = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const PANELS: [string, string, string][] = [
  ['NotificationsPanel.tsx', 'api.notificationSettings()', 'notification settings'],
  ['UpdatesPanel.tsx', 'api.updateCheck()', 'update status'],
  ['VoicePanel.tsx', "api.useCaseSettings('stt')", 'speech settings'],
]

describe('a settings panel whose gating read fails says so', () => {
  for (const [panel, call, what] of PANELS) {
    it(`${panel} lets the rejection reach the hook`, () => {
      const code = codeOf(panel)
      expect(code, `${panel} must still make the call`).toContain(call)
      const line = code.split('\n').find((l) => l.includes(call)) ?? ''
      expect(line, 'a `.catch` chained onto the gating read fabricates the panel').not.toMatch(/\.catch\(\(\)\s*=>/)
    })

    it(`${panel} renders the failure instead of a forever-skeleton`, () => {
      const code = codeOf(panel)
      expect(code).toContain(`<LoadError what="${what}" error={loadErr} onRetry={refresh} />`)
      const errAt = code.search(/<LoadError\b/)
      const skelAt = code.search(/<FormSkeleton\b/)
      expect(errAt, 'the error branch must precede the skeleton or it never runs').toBeLessThan(skelAt)
    })
  }

  it("VoicePanel's second control-feeding read is bare too", () => {
    const line = codeOf('VoicePanel.tsx').split('\n').find((l) => l.includes("api.useCaseSettings('tts')")) ?? ''
    expect(line).not.toMatch(/\.catch\(\(\)\s*=>/)
  })

  it('the decorating reads KEEP their fallbacks — this is not a no-catch sweep', () => {
    expect(codeOf('NotificationsPanel.tsx'), 'the rules matrix decorates').toMatch(/api\.notificationRules\(\)\.catch\(\(\) => null\)/)
    expect(codeOf('UpdatesPanel.tsx'), 'the changelog decorates').toMatch(/api\.changelog\(\)\.catch\(\(\) => ''\)/)
    expect(codeOf('VoicePanel.tsx'), 'the readiness chip decorates').toMatch(/api\.modelsActive\(\)\.catch\(\(\) =>/)
  })

  it('the census that found exactly these three is reproducible', () => {
    for (const [panel] of PANELS) {
      const code = codeOf(panel)
      const writes = /\bpatch\(|api\.patchConfig|api\.save\w+|api\.set\w+/.test(code)
      const controls = /<Toggle\b|<NumberField\b|<TextInput\b|<SegToggle\b|<Segmented\b/.test(code)
      const readsError = /error:\s*loadErr/.test(code)
      expect(writes, `${panel} writes`).toBe(true)
      expect(controls, `${panel} renders controls`).toBe(true)
      expect(readsError, `${panel} must now read the error`).toBe(true)
    }
  })
})
