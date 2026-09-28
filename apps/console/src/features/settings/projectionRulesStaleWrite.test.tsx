import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { basedOn, rebaseList } from '../../shared/data/staleWrite'

describe('projection-rule revision wiring', () => {
  it('preserves an independent stored rule when reapplying an add operation', () => {
    const before = ['keep']
    const mine = ['keep', 'mine']
    const reapply = rebaseList(before, mine)
    expect(reapply(['keep', 'theirs'])).toEqual(['keep', 'theirs', 'mine'])
  })

  it('reads and writes the list with its returned revision through the shared guard', () => {
    const source = readFileSync(join(process.cwd(), 'src/features/settings/ProjectionRulesPanel.tsx'), 'utf8')
    expect(source).toContain('api.projectionRules()')
    expect(source).toContain('revision: fresh.revision')
    expect(source).toContain('guard.save({ value: document.value, revision: document.revision }')
    expect(source).toContain('api.setProjectionRules(next, revision)')
    expect(basedOn('rules-rev')).toEqual({ 'If-Match': '"rules-rev"' })
  })
})
