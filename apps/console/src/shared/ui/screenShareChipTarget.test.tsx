import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the screen-share chip is hittable', () => {
  it('holds a 24px minimum height', () => {
    expect(read('shared/ui/ScreenShareChip.tsx')).toMatch(/className="inline-flex min-h-6 -my-px shrink-0/)
  })

  it('absorbs the extra 2px so the composer row does not grow', () => {
    expect(read('shared/ui/ScreenShareChip.tsx')).toMatch(/min-h-6 -my-px/)
  })

  it('is still the button that stops sharing — the target IS the control', () => {
    const code = read('shared/ui/ScreenShareChip.tsx')
    expect(code).toMatch(/aria-label="Sharing your screen with this chat — stop sharing"/)
    expect(code).toMatch(/onClick=\{onStop\}/)
  })

  it('the sibling it converges on still uses the same idiom', () => {
    expect(read('shared/ui/DegradedChip.tsx'), 'the near-identical status pill').toMatch(/min-h-6/)
  })
})
