import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { jsxTags } from '../../shared/testing/jsxContracts'


const SRC = join(process.cwd(), "src")
const code = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every sub-goal row names its own delete button', () => {
  const src = code('features/loops/LoopPlanReview.tsx')

  it('the remove button is named from the sub-goal it removes', () => {
    expect(src).toMatch(/label=\{`Remove sub-goal: \$\{s\.length > 60 \? `\$\{s\.slice\(0, 60\)\}…` : s\}`\}/)
    expect(/(?:aria-)?label="Remove sub-goal"/.test(src), 'a constant here would announce 6 identical buttons').toBe(false)
  })

  it('the add-a-sub-goal input is named', () => {
    expect(src).toMatch(/aria-label="New sub-goal"/)
  })

  it('the sibling add control still carries its label (the pattern that was already right)', () => {
    expect(src).toMatch(/<IconButton icon=\{Plus\} label="Add sub-goal"/)
  })
})

describe('no icon-only button in either plan-review surface is unnamed', () => {
  const iconOnlyButtons = (src: string) => {
    return jsxTags(src, ['button', 'SquareIconButton', 'IconButton'])
      .filter(site => site.attributes.has('icon') || /^\s*<[A-Z]\w*[^>]*\/>\s*$/.test(
        site.element.slice(site.tag.length).replace(/<\/(button|SquareIconButton|IconButton)>$/, '')))
      .map(site => ({ line: site.line, tag: site.tag,
        named: site.attributes.has('aria-label') || site.attributes.has('label') }))
  }

  for (const rel of ['features/loops/LoopPlanReview.tsx', 'features/code/CodePlanReview.tsx']) {
    it(`${rel.split('/').pop()} has no unnamed icon-only button`, () => {
      const offenders = iconOnlyButtons(code(rel)).filter((b) => !b.named)
        .map((b) => `line ${b.line}: ${b.tag.replace(/\s+/g, ' ').slice(0, 90)}`)
      expect(
        offenders,
        `An icon-only button with no aria-label is announced as just "button":\n  ${offenders.join('\n  ')}`,
      ).toEqual([])
    })
  }

  it('the rail is not vacuously green — it finds the button it guards', () => {
    const found = iconOnlyButtons(code('features/loops/LoopPlanReview.tsx'))
    expect(found.length, 'the scanner must find at least the sub-goal remove button').toBeGreaterThan(0)
    expect(found.some((b) => b.named && /Remove sub-goal/.test(b.tag))).toBe(true)
    const sample = `<button type="button" onClick={() => f()}><X size={14} /></button>`
    const s2 = iconOnlyButtons(sample)
    expect(s2.length).toBe(1)
    expect(s2[0].named).toBe(false)
  })
})
