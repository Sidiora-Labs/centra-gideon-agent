import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/settings/DoctorPanel.tsx")
const raw = readFileSync(SRC, 'utf8')
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the doctor panel reports its read failures', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(raw).toMatch(/function RemediationSection\(/)
    expect(raw.length).toBeGreaterThan(6000)
  })

  it('the report failure is announced', () => {
    expect(src).toMatch(/<div role="alert"[^>]*>Couldn't load the doctor report\./)
  })

  it('the remediation read captures its rejection instead of substituting null', () => {
    expect(/doctorRemediation\(\)[\s\S]{0,80}\.catch\(\(\) => setSnap\(null\)\)/.test(src),
      'setSnap(null) rendered "Loading…" forever').toBe(false)
    expect(src, 'the rejection must land in state').toMatch(/\.catch\(setLoadErr\)/)
  })

  it('the health score says it failed instead of pretending to load', () => {
    expect(src).toMatch(/Couldn't load the health score/)
    expect(src, 'and announces, because it replaces content the user was reading').toMatch(
      /<span role="alert">Couldn't load the health score/,
    )
  })

  it('Run now is disarmed on a read failure, and says why', () => {
    expect(src).toMatch(/disabled=\{Boolean\(loadErr\)\}/)
    expect(src).toMatch(/disabledReason=\{loadErr \? 'The health score could not be read'/)
  })

  it('and is NOT disarmed merely by the initial load', () => {
    expect(/disabled=\{[^}]*!snap[^}]*\}/.test(src), 'a loading snapshot must not disable the action').toBe(false)
  })
})
