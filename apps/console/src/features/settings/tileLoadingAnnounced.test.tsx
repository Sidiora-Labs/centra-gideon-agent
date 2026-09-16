import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Blocks } from 'lucide-react'
import { BentoCard, CardSkeleton } from './bento'

// region: it announces nothing on its own and is read only if the user lands on the control. One

describe('a loading tile says it is busy on the node AT can reach', () => {
  it('marks the nav button busy while loading', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' }).getAttribute('aria-busy')).toBe('true')
  })

  it('drops the attribute entirely once loaded — not aria-busy="false"', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()}><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' }).hasAttribute('aria-busy')).toBe(false)
  })

  it('does not touch the accessible NAME while busy', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' })).toBeTruthy()
  })

  it('keeps the skeleton itself hidden — it is decoration, not content', () => {
    const { container } = render(<CardSkeleton rows={3} />)
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelectorAll('.animate-pulse').length).toBe(3)
  })

  it('adds NO per-tile live region', () => {
    const { container } = render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>b</div></BentoCard>)
    expect(container.querySelector('[role="status"]')).toBeNull()
    expect(container.querySelector('[aria-live]')).toBeNull()
  })

  it('the source records the count that made this decision', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/bento.tsx"), 'utf8')
    expect(src).toMatch(/22 tiles shimmer/)
    expect(src).toMatch(/aria-busy=\{loading \|\| undefined\}/)
  })
})
