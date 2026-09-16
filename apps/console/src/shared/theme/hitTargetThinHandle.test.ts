import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const css = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8')
const navRail = readFileSync(join(SRC, 'shared/ui/NavRail.tsx'), 'utf8')

const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, '')

function ruleBody(selector: string): string {
  const at = cssCode.indexOf(`${selector} {`)
  expect(at, `${selector} is not declared in design/tokens.css`).toBeGreaterThan(-1)
  const end = cssCode.indexOf('}', at)
  expect(end, `${selector}'s body does not terminate`).toBeGreaterThan(at)
  return cssCode.slice(at, end)
}

describe('the thin-handle hit target', () => {
  it('reads its subjects (a rail over nothing asserts nothing)', () => {
    expect(css.length, 'tokens.css did not read').toBeGreaterThan(10_000)
    expect(navRail, 'the handle must still be a window-splitter').toContain('role="separator"')
    expect(navRail).toContain('aria-orientation="vertical"')
  })

  it('the band is 24px wide and pinned to the element\'s own height', () => {
    const before = ruleBody('.hit-24-x::before')
    expect(before, 'a generated box needs content').toMatch(/content:\s*""/)
    expect(before).toMatch(/position:\s*absolute/)
    expect(before, 'width comes from the floor, not a literal').toMatch(/width:\s*var\(--hit-min\)/)
    expect(before, 'centred, so no call site restates the control width').toMatch(/left:\s*50%/)
    expect(before).toMatch(/translateX\(-50%\)/)
    expect(before, 'top must pin to the element').toMatch(/top:\s*0/)
    expect(before, 'bottom must pin to the element').toMatch(/bottom:\s*0/)
    expect(before, 'and it must forward events, not swallow them').toMatch(/pointer-events:\s*auto/)
  })

  it('and the band must NEVER grow vertically', () => {
    const before = ruleBody('.hit-24-x::before')
    expect(before, 'no symmetric inset — that is `.hit-24`').not.toMatch(/inset:/)
    expect(before, 'no negative vertical pull').not.toMatch(/top:\s*calc\(-|bottom:\s*calc\(-|top:\s*-|bottom:\s*-/)
  })

  it('🔴 the utility does NOT set position — the call site owns its containing block', () => {
    expect(
      ruleBody('.hit-24-x'),
      'setting `position` here drops an absolutely-placed call site out of its placement — the ' +
        'first draft of this change did exactly that and the reachable band went 4px -> 1px',
    ).not.toMatch(/position:/)
    const handle = navRail.slice(navRail.indexOf('role="separator"'))
    const className = handle.slice(handle.indexOf('className='), handle.indexOf('/>') + 2)
    expect(className, 'the adopter must be positioned itself').toMatch(/\babsolute\b/)
    expect(className, 'and must carry the utility').toMatch(/\bhit-24-x\b/)
  })

  it('the handle keeps the geometry the drag maths depends on', () => {
    const handle = navRail.slice(navRail.indexOf('role="separator"'))
    const className = handle.slice(handle.indexOf('className='), handle.indexOf('/>') + 2)
    expect(className, 'still a 4px drawn strip').toMatch(/\bw-1\b/)
    expect(className, 'still full height').toMatch(/\bh-full\b/)
    expect(className, 'still pinned to the rail\'s right edge').toMatch(/\bright-0\b/)
    expect(handle).toMatch(/aria-valuenow=/)
    expect(handle).toMatch(/onKeyDown=/)
  })

  it('`.hit-24` is left alone — it is a different shape for a different problem', () => {
    const base = ruleBody('.hit-24')
    expect(base, '`.hit-24` still establishes its own containing block').toMatch(/position:\s*relative/)
    expect(base).toMatch(/--hit-size:\s*21px/)
    expect(
      readFileSync(join(SRC, 'shared/ui/BoardCollapse.tsx'), 'utf8'),
      'BoardCollapse must still use the symmetric idiom, not this one',
    ).toMatch(/\bhit-24\b(?!-x)/)
  })
})
