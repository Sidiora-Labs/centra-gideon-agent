import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const onboarding = () => readFileSync(join(SRC, 'app/shell/Onboarding.tsx'), 'utf8')
const stepRow = () => readFileSync(join(SRC, 'features/onboarding/StepStack.tsx'), 'utf8')

describe('onboarding step progress is announced', () => {
  it('the active step is marked aria-current="step"', () => {
    expect(stepRow()).toMatch(/aria-current=\{active \? 'step' : undefined\}/)
  })

  it('a polite live region announces the current step, with its number and title', () => {
    const src = onboarding()
    expect(src, 'a status live region must exist').toMatch(/role="status" aria-live="polite"/)
    expect(src).toMatch(/Step \$\{ORDER\.indexOf\(state\.step\) \+ 1\} of \$\{ORDER\.length\}: \$\{TITLES\[state\.step\]\}/)
  })

  it('the announced title comes from the SAME source as the visible one', () => {
    const src = onboarding()
    const state = readFileSync(join(SRC, 'app/shell/onboardingState.ts'), 'utf8')
    expect(state).toMatch(/const TITLES: Record<StepId, string>/)
    const steps = (state.match(/const ORDER: StepId\[\] = \[([^\]]*)\]/)?.[1] ?? '')
      .split(',').map((item) => item.trim()).filter(Boolean)
    expect(steps.length).toBe(5)
    expect(src).toContain('ORDER.map((id, index)')
    expect(src).toContain('title={TITLES[id]}')
  })

  it('the live region is visually hidden, not visible chrome', () => {
    expect(onboarding()).toMatch(/role="status" aria-live="polite" className="sr-only"/)
  })
})
