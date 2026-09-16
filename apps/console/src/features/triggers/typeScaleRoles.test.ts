import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const F = (rel: string) => readFileSync(join(process.cwd(), "src/features/triggers", rel), 'utf8')

describe('triggers pages carry no off-scale font sizes', () => {
  it('the week grid day cell rides the caption tier', () => {
    const src = F('WeekGridView.tsx')
    expect(src, 'the cell announces its role').toContain('data-type="caption"')
    expect(src, 'the sub-floor literal is gone').not.toContain('text-[0.6875rem]')
  })

  it('no triggers file uses a size that sits on no tier', () => {
    for (const rel of ['WeekGridView.tsx', 'TriggersListPage.tsx', 'StoreTriggerDetail.tsx']) {
      const src = F(rel)
      expect(src, `${rel}: 0.875rem is between body-s and body-m — on no tier`)
        .not.toContain('text-[0.875rem]')
      expect(src, `${rel}: nothing may dip under the caption floor`)
        .not.toMatch(/text-\[0\.(6|7[0-4])\d*rem\]/)
    }
  })
})
