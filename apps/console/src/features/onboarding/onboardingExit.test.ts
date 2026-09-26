import { describe, it, expect, beforeEach, vi } from 'vitest'
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
  it('keeps the full destination through a module reload', async () => {
    setOnboardingExit('#/chat/session-1?workspace=run&run=run-2')
    vi.resetModules()
    const fresh = await import('./exitTo')
    expect(fresh.peekOnboardingExit()).toBe('chat/session-1?workspace=run&run=run-2')
    fresh.clearOnboardingExit()
  })
})
