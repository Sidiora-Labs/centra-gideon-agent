import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'


const SETTINGS = join(process.cwd(), "src/features/settings")
const UI = readFileSync(join(SETTINGS, 'settingsUI.tsx'), 'utf8')
const panels = () =>
  readdirSync(SETTINGS).filter((f) => /Panel\.tsx$/.test(f) && !f.includes('.test.'))

describe('a rejected settings save names the control', () => {
  it('every shared row hands the label to the patch it fires', () => {
    expect(UI, 'the toggle must pass its label').toContain('patch(field, v as never, flash, label)')
    expect(UI, 'the number field must too').toContain('patch(field, n as never, flash, label)')
    expect(UI, 'the string list must too').toContain('patch(field, next as never, flash, label)')
    const sigs = [...UI.matchAll(/patch: \(k: string, v: never, cb: \(\) => void, label\?: string\) => void/g)]
    expect(sigs.length, 'all three row prop types carry the 4th argument').toBe(3)
  })

  it('NO save failure names a config key or path — the ratchet', () => {
    const IDENTIFIER_VARS = ['key', 'path', 'field']
    const offenders: string[] = []
    let seen = 0
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      for (const m of src.matchAll(/notify\(`Couldn't save ([^`]*)`/g)) {
        seen++
        const phrase = m[1]
        for (const v of IDENTIFIER_VARS) {
          if (new RegExp(`\\$\\{\\s*${v}\\s*\\}`).test(phrase)) {
            offenders.push(`${f}: \${${v}} — ${phrase.slice(0, 44)}`)
          }
        }
      }
    }
    expect(seen, 'the sweep must find the family it guards').toBeGreaterThanOrEqual(16)
    expect(offenders, 'a config identifier is not a name the user has seen').toEqual([])
  })

  it('the human-phrased toasts are left alone — they were already the goal', () => {
    const chat = readFileSync(join(SETTINGS, 'ChatPanel.tsx'), 'utf8')
    expect(chat).toContain("Couldn't save mid-turn policy:")
    expect(chat).toContain("Couldn't save this chat setting:")
    const account = readFileSync(join(SETTINGS, 'AccountPanel.tsx'), 'utf8')
    expect(account).toContain("Couldn't save your username:")
  })

  it('every panel that receives the label declares it', () => {
    const withToast = panels().filter((f) =>
      readFileSync(join(SETTINGS, f), 'utf8').includes('${label ?? key}'),
    )
    const withParam = panels().filter((f) =>
      /label\?: string\) => \{/.test(readFileSync(join(SETTINGS, f), 'utf8')),
    )
    expect(withToast.length, 'the family spans the panels measured').toBeGreaterThanOrEqual(8)
    expect(withToast.sort()).toEqual(withParam.sort())
  })

  it('a label is actually SUPPLIED in every panel, not merely declared', () => {
    const missing: string[] = []
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      if (!/label\?: string\) => \{/.test(src) && !/label\?: string\)/.test(src)) continue
      const supplies =
        /from '\.\/settingsUI'/.test(src) && /<(ToggleRow|NumberRow)/.test(src)
        || /undefined, l\)/.test(src)
        || /onCommit\(\w+, (label|'[^']+')\)/.test(src)
        || /patchNum\('[^']+', v, '[^']+'\)/.test(src)
      if (!supplies) missing.push(f)
    }
    expect(missing, 'these panels accept a label but nothing gives them one').toEqual([])
  })

  it('the two panels with their OWN rows forward the label through every commit', () => {
    for (const [f, expected] of [['ChatPanel.tsx', 9], ['DurabilityPanel.tsx', 3]] as const) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      const commits = [...src.matchAll(/onCommit=\{\(/g)].length
      const forwarding = [...src.matchAll(/onCommit=\{\(\w+, l\) => patch\([^)]*undefined, l\)\}/g)].length
      expect(commits, `${f}: the row usages must be discoverable`).toBeGreaterThanOrEqual(expected)
      expect(forwarding, `${f}: every commit must forward the label`).toBe(expected)
    }
    const chat = readFileSync(join(SETTINGS, 'ChatPanel.tsx'), 'utf8')
    expect(chat, 'the labelled row passes its own label').toContain('onCommit(n, label)')
    expect(chat, 'the row without a label prop supplies a literal').toContain(
      "onCommit(n, 'Auto-archive after (days)')",
    )
    const dur = readFileSync(join(SETTINGS, 'DurabilityPanel.tsx'), 'utf8')
    expect(dur).toContain('onChange={(n) => onCommit(n, label)}')
  })

  it('the fallback stays — a bare label would print "undefined"', () => {
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      for (const m of src.matchAll(/notify\(`Couldn't save \$\{([^}]*)\}/g)) {
        expect(m[1], `${f}: an interpolated save toast must fall back to the identifier`).toMatch(
          /\?\?\s*(key|path)/,
        )
      }
    }
  })

  it('GuardrailsPanel is included, though it uses a different row contract', () => {
    const g = readFileSync(join(SETTINGS, 'GuardrailsPanel.tsx'), 'utf8')
    expect(g).toContain('const patchNum = (path: string, value: number, label?: string)')
    const labelled = [...g.matchAll(/patchNum\('[^']+', v, '[^']+'\)/g)]
    expect(labelled.length, 'every patchNum call names its control').toBe(5)
  })

  it('the label is still used for accessibility, not moved off the control', () => {
    expect(UI).toContain('label={label}')
    expect(UI).toContain('ariaLabel={label}')
  })
})
