import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the degraded chip is a 24px target on every route', () => {
  const src = read('shared/ui/DegradedChip.tsx')

  it('the trigger carries a 24px minimum height', () => {
    expect(src).toMatch(/className=\{`flex min-h-6 items-center gap-1\.5 rounded-pill py-1/)
  })

  it('its width is left alone, because the shell corner width is load-bearing', () => {
    expect(src, 'still icon-only below the mobile breakpoint').toMatch(/isMobile \? 'px-1\.5' : 'px-2\.5'/)
    expect(src, 'no min-w on the trigger').not.toMatch(/min-w-6/)
  })

  it('keeps the accessible name it needs when icon-only', () => {
    expect(src).toMatch(/aria-label=\{isMobile \? summary : undefined\}/)
  })
})

describe("the tools page's discovered-servers disclosure is a 24px target", () => {
  const src = read('features/tools/ToolsPage.tsx')

  it('grows the box and hands the space back', () => {
    expect(src).toMatch(/className="mb-s flex min-h-6 -my-0\.5 items-center gap-s text-on-surface-low/)
  })

  it('is still the disclosure it was, announcing its state', () => {
    expect(src, 'aria-expanded is what makes it a disclosure').toMatch(/aria-expanded=\{open\}[^>]*className="mb-s flex min-h-6/)
  })
})

describe('the pattern these two joined', () => {
  const ADOPTERS: [string, RegExp][] = [
    ['features/loop/LoopComposer.tsx', /inline-flex min-h-6 cursor-pointer items-center/],
    ['features/dashboard/widgets/kit.tsx', /inline-flex min-h-6 -my-px items-center/],
    ['features/dashboard/DashboardPage.tsx', /inline-flex min-h-6 -my-0\.5 items-center/],
  ]
  for (const [rel, re] of ADOPTERS) {
    it(`${rel} still uses the grow-the-box idiom`, () => {
      expect(read(rel), 'the idiom moved — reconcile rather than fork it').toMatch(re)
    })
  }

  it('and the rail that recorded why a min-height is wrong for inline TEXT is intact', () => {
    const rail = read('shared/ui/nestedTargetSize.test.tsx')
    expect(rail).toMatch(/a min-height would need inline-flex, which moves the baseline/)
  })
})
