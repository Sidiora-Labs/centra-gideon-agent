import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { setOnboardingExit, peekOnboardingExit, clearOnboardingExit } from './exitTo'


beforeEach(() => { clearOnboardingExit() })

describe('the pending onboarding exit destination', () => {
  it('is empty by default, so the guard keeps its dashboard behaviour', () => {
    expect(peekOnboardingExit()).toBe('')
  })

  it('round-trips a hash path', () => {
    setOnboardingExit('settings/providers')
    expect(peekOnboardingExit()).toBe('settings/providers')
  })

  it('READING IS IDEMPOTENT — the guard may run any number of times before `route` catches up', () => {
    setOnboardingExit('settings/providers')
    for (let i = 0; i < 5; i++) {
      expect(peekOnboardingExit(), `guard run ${i + 1} must resolve the SAME destination`).toBe('settings/providers')
      expect(peekOnboardingExit() || 'dashboard').toBe('settings/providers')
    }
  })

  it('clearing is explicit, and only then does the default come back', () => {
    setOnboardingExit('settings/doctor')
    clearOnboardingExit()
    expect(peekOnboardingExit()).toBe('')
    expect(peekOnboardingExit() || 'dashboard').toBe('dashboard')
  })

  it('normalises a leading `#/` or `/` so callers can pass either spelling', () => {
    setOnboardingExit('#/knowledge/item/abc')
    expect(peekOnboardingExit()).toBe('knowledge/item/abc')
    setOnboardingExit('/loops/lp-1')
    expect(peekOnboardingExit()).toBe('loops/lp-1')
  })
})

describe('the guard consumes it', () => {
  const app = () => readFileSync(join(process.cwd(), "src/app/shell/App.tsx"), 'utf8')

  it('the exit branch PEEKS, defaulting to the dashboard', () => {
    expect(app()).toMatch(
      /onboarded && route === 'onboarding'\)\s*navigate\(peekOnboardingExit\(\) \|\| 'dashboard'\)/)
  })

  it('the exit branch must NOT consume — a clearing read reintroduces the measured bug', () => {
    expect(app()).not.toMatch(/navigate\(takeOnboardingExit\(\)/)
  })

  it('clearing happens on a LATER branch, once the route has left onboarding', () => {
    expect(app()).toMatch(/else if \(onboarded\) clearOnboardingExit\(\)/)
  })

  it('the redirect INTO onboarding is untouched — the gate still holds', () => {
    expect(app()).toMatch(/!onboarded && route !== 'onboarding'\) navigate\('onboarding'\)/)
  })

  it('the flow hands the destination over and then finishes, in that order', () => {
    const src = readFileSync(join(process.cwd(), "src/app/shell/Onboarding.tsx"), 'utf8')
    const body = src.match(/function exitTo\(path: string\) \{[\s\S]*?\n  \}/)?.[0] ?? ''
    expect(body, 'exitTo must exist').toContain('setOnboardingExit(path)')
    expect(body.indexOf('setOnboardingExit')).toBeLessThan(body.indexOf('finish()'))
  })
})
