import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeListPage.tsx"), 'utf8')

describe('the selected filter chip', () => {
  it('uses the accent CONTAINER pair, not the accent as ink', () => {
    expect(SRC).toMatch(/background: 'var\(--color-primary-container\)'/)
    expect(SRC).toMatch(/color: 'var\(--color-on-primary-container\)'/)
  })

  it('no longer paints a primary tint under primary ink', () => {
    expect(
      /color-mix\(in srgb, \$\{tone \?\? 'var\(--color-primary\)'\} 20%/.test(SRC),
      'the accent must not be both the tint and the ink',
    ).toBe(false)
  })

  it('leaves a type-toned chip exactly as it was', () => {
    expect(SRC).toMatch(/background: `color-mix\(in srgb, \$\{tone\} 20%, transparent\)`, color: tone/)
  })

  it('keeps the unselected chip neutral', () => {
    expect(SRC).toMatch(/background: 'var\(--color-surface-high\)', color: 'var\(--color-on-surface-var\)'/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(SRC.length).toBeGreaterThan(2000)
    expect(SRC).toMatch(/function FilterChip\(/)
  })
})
