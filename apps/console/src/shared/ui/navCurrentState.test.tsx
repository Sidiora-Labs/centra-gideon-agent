import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NavRail } from './NavRail'
import { Home, Inbox } from 'lucide-react'


const SRC = join(process.cwd(), "src")
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const ITEMS = [
  { id: 'home', label: 'Home', icon: Home },
  { id: 'inbox', label: 'Inbox', icon: Inbox },
]

describe('NavRail announces which item is current', () => {
  it('exactly the active item carries aria-current="page"', () => {
    const { container } = render(
      <NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />,
    )
    const current = [...container.querySelectorAll('[aria-current="page"]')]
    expect(current).toHaveLength(1)
    expect(current[0].getAttribute('aria-label')).toBe('Inbox')
  })

  it('the inactive item carries NO aria-current at all', () => {
    const { container } = render(
      <NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />,
    )
    const home = container.querySelector('[aria-label="Home"]')!
    expect(home.hasAttribute('aria-current')).toBe(false)
  })

  it('it FOLLOWS activeId rather than being pinned to one item', () => {
    const { container, rerender } = render(
      <NavRail items={ITEMS} activeId="home" onSelect={() => {}} collapsed={false} />,
    )
    expect(container.querySelector('[aria-current="page"]')?.getAttribute('aria-label')).toBe('Home')
    rerender(<NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />)
    expect(container.querySelector('[aria-current="page"]')?.getAttribute('aria-label')).toBe('Inbox')
    expect(container.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
  })

  it('the announcement agrees with the VISUAL active state', () => {
    const src = strip(readFileSync(join(SRC, 'shared/ui/NavRail.tsx'), 'utf8'))
    expect(src).toMatch(/const active = item\.id === activeId/)
    expect(src).toMatch(/aria-current=\{active \? 'page' : undefined\}/)
    expect(src).toMatch(/withWeight\(\{ height: 32 \}, active \? 470 : 400\)/)
  })

  it('the nav uses aria-current, NOT aria-selected', () => {
    const src = strip(readFileSync(join(SRC, 'shared/ui/NavRail.tsx'), 'utf8'))
    expect(/aria-selected/.test(src), 'navigation wants aria-current, not aria-selected').toBe(false)
  })
})
