import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RowAction } from './widgets/kit'


describe('RowAction is a reachable target', () => {
  it('has a 24px-tall hit box', () => {
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/\bmin-h-6\b/)
  })

  it('returns the 2px to the layout, so no widget row reflows', () => {
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/-my-px/)
  })

  it('keeps its painted padding — the design is unchanged', () => {
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    const cls = screen.getByRole('button').className
    expect(cls).toMatch(/px-m/)
    expect(cls).toMatch(/py-xs/)
  })

  it('still carries the tone it was asked for', () => {
    render(<RowAction tone="danger" onClick={vi.fn()} title="Open doctor">x</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/text-danger/)
  })
})

describe('the fix reaches the widgets, and the chips too', () => {
  const DIR = join(process.cwd(), "src/features/dashboard")

  it('RowAction has enough adopters to be worth fixing once (not vacuously green)', () => {
    const count = readdirSync(join(DIR, 'widgets'))
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
      .reduce((sum, n) => sum + (readFileSync(join(DIR, 'widgets', n), 'utf8').match(/<RowAction\b/g) ?? []).length, 0)
    expect(count, 'the primitive must actually be in use across the widgets').toBeGreaterThanOrEqual(6)
  })

  it('the "Jump back in" chips grew too', () => {
    const src = readFileSync(join(DIR, 'DashboardPage.tsx'), 'utf8')
    expect(src).toMatch(/inline-flex min-h-6 -my-0\.5 items-center gap-xs text-on-surface-var/)
  })

  it('does not silence the Reply CONTRAST finding, which is a separate owner call', () => {
    const src = readFileSync(join(DIR, 'widgets', 'ActionCenter.tsx'), 'utf8')
    expect(src, 'the tone is untouched — only the hit box moved').toMatch(/<RowAction tone="primary"/)
  })
})
