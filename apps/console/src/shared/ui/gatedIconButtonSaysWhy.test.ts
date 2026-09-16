import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// three are genuine unavailability (a license gate, `disabled={false}`, an already-pinned widget).

const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function gatedTags(src: string): string[] {
  return [...src.matchAll(/<(?:Square)?IconButton\b[\s\S]{0,500}?\/>/g)]
    .map((m) => m[0])
    .filter((t) => /(?<!aria-)disabled=/.test(t))
}

describe('a gated icon button whose gate the user can fix says so', () => {
  const ADOPTERS: [string, RegExp][] = [
    ['features/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{busy\}/],
    ['features/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{sending\}/],
    ['shared/ui/FindBar.tsx', /label="Previous match"/],
    ['shared/ui/FindBar.tsx', /label="Next match"/],
  ]

  for (const [rel, gate] of ADOPTERS) {
    it(`${rel} ${gate.source.slice(0, 34)}… names what to do`, () => {
      const tag = gatedTags(readFileSync(join(SRC, rel), 'utf8')).find((t) => gate.test(t))
      expect(tag, `the gated button matching ${gate} must still exist`).toBeTruthy()
      expect(tag!, 'a fixable gate must say what fixes it').toMatch(/disabledReason=/)
    })
  }

  it('the cockpit reason is conditional, so it never fires mid-send', () => {
    const src = readFileSync(join(SRC, 'features/code/CodeCockpitPage.tsx'), 'utf8')
    const conditional = [...src.matchAll(/disabledReason=\{!text\.trim\(\) \? 'Type a steer first' : undefined\}/g)]
    expect(conditional.length, 'both steer composers gate the reason on the fixable branch only').toBe(2)
  })

  it('it converges on the canonical composer, which had it first', () => {
    const composer = readFileSync(join(SRC, 'shared/ui/Composer.tsx'), 'utf8')
    expect(composer, "the canonical send button's reason is the model for the others").toMatch(
      /label="Send message" disabledReason="Type a bit more first"/,
    )
  })

  it('a self-evident gate is still left mute — and every one left is a REAL gate', () => {
    // row explains itself by position, and a license-gated download by the badge beside it.
    const mute = walk(SRC).flatMap((abs) => gatedTags(readFileSync(abs, 'utf8')))
      .filter((t) => !/disabledReason=/.test(t))
    // ⚠️ note at the top): a license gate, `disabled={false}`, and an already-pinned widget.
    expect(mute.length, 'the self-evident gates keep their silence deliberately')
      .toBeGreaterThanOrEqual(3)
    const inFlight = mute.filter((t) => /(?<!aria-)disabled=\{[^}]*(?:busy|saving|sending|testing|rechecking|reconnecting|deleting|pending|loading)/i.test(t))
    expect(inFlight, 'an in-flight gate belongs on `loading`, not `disabled`').toEqual([])
  })

  it('both icon primitives keep the tab stop, which is what makes a reason audible at all', () => {
    for (const rel of ['shared/ui/IconButton.tsx', 'shared/ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must map disabled to aria-disabled`).toMatch(/aria-disabled=\{disabled \|\| undefined\}/)
      expect(src, `${rel} must not emit the native attribute`).not.toMatch(/<motion\.button[\s\S]{0,300}?\sdisabled=\{/)
    }
  })
})
