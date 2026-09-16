import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const routing = () => readFileSync(join(SRC, 'features/settings/RoutingPanel.tsx'), 'utf8')

function moveButtons(src: string): string[] {
  return src
    .split('<button type="button"')
    .slice(1)
    .map((chunk) => chunk.slice(0, chunk.indexOf('</button>')))
    .filter((el) => /aria-label=\{`Move /.test(el))
}

describe('reorder buttons carry a 24px+ target', () => {
  it('finds both reorder buttons (not vacuously green)', () => {
    expect(moveButtons(routing()).length, 'both the earlier and later button must be matched').toBe(2)
  })

  it('each uses the 28px square geometry, not p-1', () => {
    for (const tag of moveButtons(routing())) {
      expect(tag, 'must be a 28px grid-centred square').toMatch(/grid size-7 place-items-center/)
      expect(/\bp-1\b/.test(tag), '21px padding-only geometry must not come back').toBe(false)
    }
  })

  it('the size matches the icon-button primitive it borrows from', () => {
    const sib = readFileSync(join(SRC, 'shared/ui/SquareIconButton.tsx'), 'utf8')
    expect(sib).toMatch(/grid size-7 place-items-center rounded-md/)
  })

  it('the busy semantics are preserved — still unavailableWhen, not the primitive', () => {
    const src = routing()
    expect(src).toMatch(/unavailableWhen\(i === 0, 'Already tried first', \{ busy \}\)/)
    expect(src).toMatch(/unavailableWhen\(i === shown\.length - 1, 'Already tried last', \{ busy \}\)/)
    const helper = readFileSync(join(SRC, 'shared/ui/unavailable.ts'), 'utf8')
    expect(helper, 'busy must still mean NATIVE disabled').toMatch(/if \(opts\?\.busy\) return \{ disabled: true/)
  })

  it('each button still names itself and hides its icon', () => {
    for (const tag of moveButtons(routing())) expect(tag).toMatch(/aria-label=\{`Move \$\{ref\} (earlier|later)`\}/)
    expect(routing()).toMatch(/<ArrowUp size=\{13\} aria-hidden \/>/)
    expect(routing()).toMatch(/<ArrowDown size=\{13\} aria-hidden \/>/)
  })
})
