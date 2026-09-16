import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const onboarding = () => readFileSync(join(SRC, 'app/shell/Onboarding.tsx'), 'utf8')

describe('the onboarding name field is programmatically labelled', () => {
  it('the input carries a real label, not just a placeholder', () => {
    const src = onboarding()
    const inputTag = src.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(inputTag, 'the name input must exist').toContain('placeholder="Your name"')
    expect(
      /aria-label=|aria-labelledby=/.test(inputTag),
      'the name input must have a programmatic label, not placeholder-only',
    ).toBe(true)
  })

  it('the accessible name matches the visible title source (no Label-in-Name conflict)', () => {
    const src = onboarding() + readFileSync(join(SRC, 'app/shell/onboardingState.ts'), 'utf8')
    expect(src, 'TITLES.name is the visible title').toMatch(/name: 'Your name'/)
    expect(src, 'the input names itself the same').toMatch(/aria-label="Your name"/)
  })

  it('the matcher would fail on the placeholder-only shape it replaced', () => {
    const before = '<input autoFocus placeholder="Your name" className="…" />'
    const tag = before.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(/aria-label=|aria-labelledby=/.test(tag)).toBe(false)
  })
})
